"""DEV-ONLY mock backend for the web cabinet. Never part of the request path.

Phase 6 is built ahead of phases 2-4 on purpose, so this script stands in for
`src/api.py` until it exists: it serves `static/` at `/ui` and answers the
subset of contract 9.5 / 9.7 that the cabinet actually calls, using the JSON
fixtures in `fixtures/`. Every response is shaped exactly like the contract —
if it ever diverges, the fixture is the bug, not the page.

Nothing here is imported by the real service. `src/api.py` (service-dev) mounts
only `src.webui.STATIC_DIR`; this module is a developer tool.

    python src/webui/devserver.py            # http://127.0.0.1:8919/ui/?token=...
    python src/webui/devserver.py --port 9000 --noise 8

Stdlib only, including the WebSocket framing — a mock must not add a
dependency the product does not have.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import struct
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
FIXTURES = HERE / "fixtures"

# A real token is 48 hex chars (contract 9.1); this one is fixed so the dev URL
# is stable across restarts.
DEV_TOKEN = "de7a" * 12

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SERVICE_VERSION = "3.0.0-mock"

STARTED_AT = time.time()

# --------------------------------------------------------------------------
# fixture state (mutable — reindex actually moves it)
# --------------------------------------------------------------------------

_lock = threading.RLock()


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


BANKS: list[dict] = _load("banks.json")["banks"]
TREES: dict[str, dict] = _load("trees.json")
FILES: dict[str, dict] = _load("files.json")
_logs = _load("logs.json")
QUERY_EVENTS: list[dict] = _logs["query"]
INDEX_EVENTS: list[dict] = _logs["index"]

_next_index_id = max((e["id"] for e in INDEX_EVENTS), default=0) + 1
_next_query_id = max((e["id"] for e in QUERY_EVENTS), default=0) + 1
_task_seq = 0

QUEUE: dict[str, Any] = {"depth": 0, "high": 0, "normal": 0, "low": 0, "current": None}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def now_iso_ms() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def find_bank(ref: str) -> dict | None:
    """Contract 6.4: id, then name, then path (exact root or deepest ancestor)."""
    ref = (ref or "").strip()
    if not ref:
        return None
    with _lock:
        for bank in BANKS:
            if bank["id"] == ref:
                return bank
        for bank in BANKS:
            if bank["name"].strip().lower() == ref.lower():
                return bank
        posix = ref.replace("\\", "/").rstrip("/").lower()
        best = None
        for bank in BANKS:
            root = bank["root"].rstrip("/").lower()
            if posix == root or posix.startswith(root + "/"):
                if best is None or len(root) > len(best["root"]):
                    best = bank
        return best


# --------------------------------------------------------------------------
# WebSocket plumbing (RFC 6455, the small subset a browser needs)
# --------------------------------------------------------------------------


class WSClient:
    def __init__(self, conn, bank_filter: str | None) -> None:
        self.conn = conn
        self.bank_filter = bank_filter
        self.send_lock = threading.Lock()
        self.alive = True

    def send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 1 << 16:
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        with self.send_lock:
            self.conn.sendall(bytes(header) + payload)

    def send_json(self, obj: dict) -> None:
        self.send_frame(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


CLIENTS: list[WSClient] = []
_clients_lock = threading.Lock()


def broadcast(event_type: str, bank_id: str | None, data: dict) -> None:
    envelope = {
        "v": 1,
        "type": event_type,
        "ts": now_iso_ms(),
        "bank_id": bank_id,
        "data": data,
    }
    with _clients_lock:
        targets = list(CLIENTS)
    for client in targets:
        if client.bank_filter and bank_id and client.bank_filter != bank_id:
            continue
        try:
            client.send_json(envelope)
        except OSError:
            client.alive = False


def read_frame(rfile) -> tuple[int, bytes] | None:
    head = rfile.read(2)
    if len(head) < 2:
        return None
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rfile.read(8))[0]
    mask = rfile.read(4) if masked else b""
    payload = rfile.read(length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def queue_snapshot() -> dict:
    with _lock:
        return dict(QUEUE)


def set_queue(**fields) -> None:
    with _lock:
        QUEUE.update(fields)
    broadcast("queue", None, queue_snapshot())


# --------------------------------------------------------------------------
# simulated indexing
# --------------------------------------------------------------------------


def _task_id() -> str:
    global _task_seq
    with _lock:
        _task_seq += 1
        return f"{_task_seq:04x}" + "c1e" + f"{int(time.time()) % 0x10000:04x}"


def _log_index(bank_id: str, **fields) -> dict:
    global _next_index_id
    with _lock:
        row = {
            "id": _next_index_id,
            "ts": now_iso(),
            "bank_id": bank_id,
            "kind": fields.get("kind", "file"),
            "trigger": fields.get("trigger", "ui"),
            "path": fields.get("path"),
            "result": fields.get("result", "ok"),
            "files_indexed": fields.get("files_indexed", 0),
            "chunks_indexed": fields.get("chunks_indexed", 0),
            "files_pruned": fields.get("files_pruned", 0),
            "took_ms": fields.get("took_ms"),
            "error": fields.get("error"),
        }
        _next_index_id += 1
        INDEX_EVENTS.insert(0, row)
    return row


def bank_info(bank: dict) -> dict:
    """BankInfo (contract 9.5) — the fixture row is already exactly that shape."""
    with _lock:
        return dict(bank)


def simulate_task(bank: dict, *, kind: str, path: str | None, batch_ms: int) -> None:
    """Walk one reindex through the WS event sequence of contract 9.7."""
    bank_id = bank["id"]
    files = FILES.get(bank_id, {})
    targets = [path] if path else [p for p, rec in files.items() if rec["indexed"]]
    started = time.time()

    with _lock:
        bank["status"] = "indexing"
        bank["indexing"] = True
        bank["queued"] = len(targets)
        bank["last_error"] = None
    broadcast("bank_status", bank_id, {"bank": bank_info(bank)})
    set_queue(depth=len(targets), high=0, normal=1 if path else 0,
              low=0 if path else len(targets), current=None)

    total_chunks = 0
    for position, rel in enumerate(targets):
        record = files.get(rel)
        chunk_total = len(record["chunks"]) if record else 3
        batches = max(1, (chunk_total + 1) // 2)
        task_id = _task_id()

        set_queue(depth=len(targets) - position, high=0,
                  normal=1 if path else 0, low=0 if path else len(targets) - position,
                  current={"task_id": task_id, "bank_id": bank_id, "kind": "file",
                           "path": rel, "batch": 0, "batches": batches,
                           "started_at": now_iso()})
        broadcast("index_start", bank_id, {"task_id": task_id, "kind": "file",
                                           "path": rel, "batches": batches,
                                           "trigger": "ui"})
        done = 0
        for batch in range(1, batches + 1):
            time.sleep(batch_ms / 1000.0)
            done = min(chunk_total, done + 2)
            broadcast("index_progress", bank_id,
                      {"task_id": task_id, "path": rel, "batch": batch,
                       "batches": batches, "chunks_done": done,
                       "chunks_total": chunk_total})

        total_chunks += chunk_total
        took = (time.time() - started) * 1000.0
        broadcast("index_done", bank_id,
                  {"task_id": task_id, "kind": "file", "path": rel,
                   "chunks_indexed": chunk_total, "files_indexed": 1, "took_ms": took})
        _log_index(bank_id, kind="file", trigger="ui", path=rel, result="ok",
                   files_indexed=1, chunks_indexed=chunk_total, took_ms=took)

        with _lock:
            bank["queued"] = len(targets) - position - 1
        broadcast("bank_status", bank_id, {"bank": bank_info(bank)})

    took = (time.time() - started) * 1000.0
    with _lock:
        bank["status"] = "ready" if total_chunks else "empty"
        bank["indexing"] = False
        bank["queued"] = 0
        bank["last_indexed"] = now_iso()
        if not path:
            bank["chunks"] = total_chunks
            bank["files"] = len(files)  # every .md in the bank, indexed or not
    set_queue(depth=0, high=0, normal=0, low=0, current=None)
    broadcast("bank_status", bank_id, {"bank": bank_info(bank)})

    if not path:
        _log_index(bank_id, kind=kind, trigger="ui", path=None, result="ok",
                   files_indexed=len(targets), chunks_indexed=total_chunks,
                   took_ms=took)
        broadcast("index_done", bank_id,
                  {"task_id": _task_id(), "kind": kind, "path": None,
                   "chunks_indexed": total_chunks, "files_indexed": len(targets),
                   "took_ms": took})


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mnemo-mock/0"
    batch_ms = 260

    # -- helpers ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib name
        print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json_out(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def fail(self, status: int, code: str, message: str, detail: Any = None) -> None:
        self.json_out(status, {"error": {"code": code, "message": message,
                                         "detail": detail}})

    def authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:].strip() == DEV_TOKEN
        return self.headers.get("X-Mnemo-Token", "").strip() == DEV_TOKEN

    # -- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/ws":
            self.handle_ws(query)
            return
        if path == "/health":
            self.json_out(200, self.health())
            return
        if path == "/" or path == "/ui":
            self.send_response(302)
            self.send_header("Location", "/ui/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/ui"):
            self.serve_static(path)
            return
        if path.startswith("/api/"):
            if not self.authorised():
                self.fail(401, "unauthorized", "missing or invalid token")
                return
            self.handle_api_get(path, query)
            return
        self.fail(404, "internal", "no route " + path)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib name
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.fail(404, "internal", "no route " + parsed.path)
            return
        if not self.authorised():
            self.fail(401, "unauthorized", "missing or invalid token")
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self.fail(422, "validation_error", "body is not valid JSON")
            return

        if parsed.path == "/api/reindex":
            self.post_reindex(body)
            return
        self.fail(404, "internal", "no route " + parsed.path)

    # -- static ----------------------------------------------------------

    def serve_static(self, path: str) -> None:
        rel = unquote(path[len("/ui"):]).lstrip("/")
        if not rel:
            rel = "index.html"
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # -- api -------------------------------------------------------------

    def health(self) -> dict:
        return {"ok": True, "version": SERVICE_VERSION, "pid": 0, "port": self.server.server_port,
                "uptime_s": round(time.time() - STARTED_AT, 1), "banks": len(BANKS),
                "queue_depth": queue_snapshot()["depth"],
                "embed": {"provider": "local", "reachable": True,
                          "host": "127.0.0.1", "port": 8917}}

    def handle_api_get(self, path: str, query: dict) -> None:
        if path == "/api/banks":
            self.json_out(200, {"banks": [bank_info(b) for b in BANKS]})
            return

        if path.startswith("/api/banks/"):
            bank = find_bank(path[len("/api/banks/"):])
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches", {"ref": path})
                return
            self.json_out(200, bank_info(bank))
            return

        if path == "/api/status":
            self.json_out(200, {
                "service": {"version": SERVICE_VERSION, "pid": 0,
                            "port": self.server.server_port,
                            "started_at": datetime.fromtimestamp(
                                STARTED_AT, timezone.utc).astimezone().isoformat(
                                    timespec="seconds"),
                            "uptime_s": round(time.time() - STARTED_AT, 1),
                            "provider": "local", "priority_enabled": True,
                            "embed": {"reachable": True, "host": "127.0.0.1",
                                      "port": 8917}},
                "queue": queue_snapshot(),
                "banks": [bank_info(b) for b in BANKS],
            })
            return

        if path == "/api/tree":
            bank = find_bank((query.get("bank") or [""])[0])
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches",
                          {"ref": (query.get("bank") or [""])[0]})
                return
            self.json_out(200, TREES[bank["id"]])
            return

        if path == "/api/file":
            bank = find_bank((query.get("bank") or [""])[0])
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches",
                          {"ref": (query.get("bank") or [""])[0]})
                return
            rel = (query.get("path") or [""])[0]
            if rel.startswith("/") or ".." in rel.split("/"):
                self.fail(400, "path_outside_bank", "path escapes the bank root",
                          {"path": rel})
                return
            record = FILES.get(bank["id"], {}).get(rel)
            if not record:
                self.fail(404, "file_not_found", "no such file in the bank",
                          {"path": rel})
                return
            self.json_out(200, record)
            return

        if path == "/api/logs":
            self.get_logs(query)
            return

        self.fail(404, "internal", "no route " + path)

    def get_logs(self, query: dict) -> None:
        kind = (query.get("kind") or [""])[0]
        if kind not in ("query", "index"):
            self.fail(400, "bad_request", "kind must be 'query' or 'index'",
                      {"kind": kind})
            return
        limit = int((query.get("limit") or ["200"])[0])
        offset = int((query.get("offset") or ["0"])[0])

        with _lock:
            rows = list(QUERY_EVENTS if kind == "query" else INDEX_EVENTS)

        ref = (query.get("bank") or [None])[0]
        if ref:
            bank = find_bank(ref)
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches", {"ref": ref})
                return
            rows = [r for r in rows if r["bank_id"] == bank["id"]]

        total = len(rows)
        self.json_out(200, {"kind": kind, "total": total, "limit": limit,
                            "offset": offset, "events": rows[offset:offset + limit]})

    def post_reindex(self, body: dict) -> None:
        ref = body.get("bank") or ""
        bank = find_bank(ref)
        if not bank:
            self.fail(404, "bank_not_found", f"no bank matches {ref!r}", {"ref": ref})
            return
        if bank.get("enabled") is False:
            self.fail(400, "bad_request", "bank is disabled", {"bank": bank["name"]})
            return

        rel = body.get("path")
        full = bool(body.get("full"))
        files = FILES.get(bank["id"], {})
        if rel and rel not in files:
            self.fail(404, "file_not_found", "no such file in the bank", {"path": rel})
            return

        kind = "file" if rel else ("rebuild" if full else "bulk")
        queued = 1 if rel else max(1, len([p for p, r in files.items() if r["indexed"]]))
        task_id = _task_id()

        threading.Thread(
            target=simulate_task,
            args=(bank,),
            kwargs={"kind": kind, "path": rel, "batch_ms": Handler.batch_ms},
            daemon=True,
        ).start()

        self.json_out(202, {"ok": True, "task_ids": [task_id], "queued": queued})

    # -- websocket -------------------------------------------------------

    def handle_ws(self, query: dict) -> None:
        token = (query.get("token") or [""])[0]
        if token != DEV_TOKEN:
            self.fail(401, "unauthorized", "missing or invalid token")
            return
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or "websocket" not in self.headers.get("Upgrade", "").lower():
            self.fail(400, "bad_request", "not a websocket upgrade")
            return

        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        self.wfile.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n"
        )
        self.wfile.flush()

        client = WSClient(self.connection, (query.get("bank") or [None])[0])
        with _clients_lock:
            CLIENTS.append(client)
        self.log_message("ws open (clients=%d)", len(CLIENTS))

        try:
            client.send_json({
                "v": 1, "type": "hello", "ts": now_iso_ms(), "bank_id": None,
                "data": {"version": SERVICE_VERSION,
                         "banks": [b["id"] for b in BANKS],
                         "queue": queue_snapshot()},
            })
            while True:
                frame = read_frame(self.rfile)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    client.send_frame(payload, opcode=0xA)
                # 0x1 text frames are subscribe/pong only — nothing to do here.
        except OSError:
            pass
        finally:
            with _clients_lock:
                if client in CLIENTS:
                    CLIENTS.remove(client)
            self.close_connection = True
            self.log_message("ws close (clients=%d)", len(CLIENTS))


# --------------------------------------------------------------------------
# background noise
# --------------------------------------------------------------------------


def heartbeat() -> None:
    while True:
        time.sleep(30)
        broadcast("ping", None, {})


def query_noise(period: float) -> None:
    """Emit a synthetic `query` WS event so the live log strip can be seen."""
    global _next_query_id
    samples = [("mcp", "як влаштована черга"), ("hook", "порт бекенда"),
               ("cli", "chunk viz"), ("ui", "реіндекс банку")]
    i = 0
    while True:
        time.sleep(period)
        face, text = samples[i % len(samples)]
        i += 1
        bank = BANKS[i % 2]
        row = {"id": _next_query_id, "ts": now_iso(), "bank_id": bank["id"],
               "face": face, "query": text, "path_prefix": None,
               "status": bank["status"], "n_hits": (i % 4), "took_ms": 20.0 + i,
               "hits": []}
        with _lock:
            _next_query_id += 1
            QUERY_EVENTS.insert(0, row)
        broadcast("query", bank["id"], {"face": face, "query": text,
                                        "status": bank["status"],
                                        "n_hits": row["n_hits"],
                                        "took_ms": row["took_ms"]})


def main() -> None:
    parser = argparse.ArgumentParser(description="dev-only mock backend for src/webui")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8919)
    parser.add_argument("--noise", type=float, default=0.0,
                        help="seconds between synthetic query events (0 = off)")
    parser.add_argument("--batch-ms", type=int, default=260,
                        help="simulated time per index batch")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit("dev server binds loopback only")

    Handler.batch_ms = args.batch_ms

    threading.Thread(target=heartbeat, daemon=True).start()
    if args.noise > 0:
        threading.Thread(target=query_noise, args=(args.noise,), daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"mock backend on http://{args.host}:{args.port}", flush=True)
    print(f"open  http://{args.host}:{args.port}/ui/?token={DEV_TOKEN}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
