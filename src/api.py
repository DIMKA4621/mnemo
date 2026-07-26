"""The backend: one loopback HTTP service, one writer, many thin faces.

Everything mnemo does from the outside — MCP, CLI, hooks, the web cabinet —
is a client of this app (Memory-contracts-v3 §9). It owns the registry, the
journal and (from phase 3) the queue and the watcher; nothing else writes an
index.

Shape of the thing:

* bound to ``127.0.0.1`` and guarded by a bearer token in
  ``STATE_DIR/api.token`` — everything under ``/api`` and ``/mcp`` needs it,
  ``/health`` and the ``/ui`` assets do not (``service_ctl`` must be able to
  ask "are you alive" before it knows where the token lives);
* one error envelope for every failure (§9.2), so a client never has to guess
  whether a 4xx body is a string, a list or a FastAPI ``detail``;
* search answers with an explicit **state** — ``indexing`` / ``empty`` /
  ``ready`` — because "nothing indexed yet" and "no match" are different
  facts and an agent must act differently on them.

Phase 2 builds the registry, the journal and the endpoints. The queue and the
watcher arrive in phase 3: the WebSocket channel is wired here, but nothing
produces progress events yet, and the seams onto ``workqueue`` are guarded
imports rather than stand-ins — a missing component reports itself instead of
returning plausible-looking nothing.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, registry, servicelog, store
from .config import STATE_DIR, TOP_K
from .providers import EmbeddingUnavailable, get_provider
from .registry import AmbiguousBankRef, Bank, BankExists, BankNotFound

log = logging.getLogger("mnemo.api")

# --------------------------------------------------------------- settings
# Resolved from config once its `api / websocket (J)` section lands; until
# then from the environment, with the contract's defaults (§12).


def _cfg(name: str, env: str, default: Any, cast: Any = str) -> Any:
    configured = getattr(config, name, None)
    if configured is not None:
        return configured
    raw = os.environ.get(env)
    if raw is None:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


SERVICE_VERSION: str = getattr(config, "SERVICE_VERSION", "3.0.0")
API_HOST: str = _cfg("API_HOST", "MNEMO_API_HOST", "127.0.0.1")
API_PORT: int = int(_cfg("API_PORT", "MNEMO_API_PORT", 8918, int))
API_TOKEN_FILE: Path = Path(
    getattr(config, "API_TOKEN_FILE", None) or STATE_DIR / "api.token"
)
SERVICE_INFO_FILE: Path = Path(
    getattr(config, "SERVICE_INFO_FILE", None) or STATE_DIR / "service.json"
)
FILE_MAX_BYTES: int = int(
    _cfg("FILE_MAX_BYTES", "MNEMO_FILE_MAX_BYTES", 2 * 1024 * 1024, int)
)
WS_PING_INTERVAL_S: float = float(getattr(config, "WS_PING_INTERVAL_S", 30.0))
WEBUI_DIR: Path = Path(__file__).resolve().parent / "webui"

_started_at = time.time()
_started_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


# ------------------------------------------------------------------ token


_token: str | None = None


def api_token() -> str:
    """The bearer token, created on first use.

    ``$MNEMO_API_TOKEN`` wins over the file so a container or a test can pin
    one without touching state.
    """
    global _token
    if _token:
        return _token
    env = os.environ.get("MNEMO_API_TOKEN")
    if env and env.strip():
        _token = env.strip()
        return _token
    try:
        existing = API_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        _token = existing
        return _token
    _token = secrets.token_hex(24)  # 48 hex chars, same shape as embed.token
    API_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    API_TOKEN_FILE.write_text(_token, encoding="utf-8")
    with suppress(OSError):
        os.chmod(API_TOKEN_FILE, 0o600)
    return _token


def _token_ok(presented: str | None) -> bool:
    if not presented:
        return False
    return secrets.compare_digest(presented.strip(), api_token())


# ------------------------------------------------------------------ errors


_ERROR_STATUS: dict[str, int] = {
    "unauthorized": 401,
    "bad_request": 400,
    "validation_error": 422,
    "bank_not_found": 404,
    "bank_ambiguous": 409,
    "bank_exists": 409,
    "root_not_found": 400,
    "path_outside_bank": 400,
    "file_not_found": 404,
    "embed_unavailable": 503,
    "internal": 500,
}


class ApiError(Exception):
    """A failure with a code from the §9.2 table."""

    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or None
        self.status = _ERROR_STATUS.get(code, 500)


def _envelope(code: str, message: str, detail: Any = None) -> dict:
    body: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return {"error": body}


# ------------------------------------------------------- queue (phase 3)


_queue_mod: Any = None
_queue_checked = False


def _queue() -> Any:
    """The work queue, once phase 3 ships it.

    A guarded import rather than a stub: with no queue running, "0 queued" is
    the truth, but *enqueueing* has nowhere to go and must say so rather than
    return a task id for work nobody will do.
    """
    global _queue_mod, _queue_checked
    if not _queue_checked:
        _queue_checked = True
        try:
            from . import workqueue as _wq  # noqa: PLC0415
        except ImportError:
            _wq = None
        _queue_mod = _wq
    return _queue_mod


def _queued(bank_id: str | None = None) -> int:
    q = _queue()
    if q is None:
        return 0
    try:
        return int(q.depth(bank_id))
    except Exception:  # noqa: BLE001
        return 0


def _busy(bank_id: str | None = None) -> bool:
    q = _queue()
    if q is None:
        return False
    try:
        return bool(q.busy(bank_id))
    except Exception:  # noqa: BLE001
        return False


def _require_queue() -> Any:
    q = _queue()
    if q is None:
        raise ApiError(
            "internal",
            "the work queue is not running: indexing is unavailable "
            "(src/workqueue.py lands in phase 3)",
        )
    return q


# ---------------------------------------------------------------- helpers


def _resolve_bank(ref: str, *, require_enabled: bool = True) -> Bank:
    try:
        bank = registry.resolve(ref)
    except AmbiguousBankRef as exc:
        raise ApiError("bank_ambiguous", str(exc), ref=ref) from exc
    except BankNotFound as exc:
        raise ApiError("bank_not_found", str(exc), ref=ref) from exc
    if require_enabled and not bank.enabled:
        # A disabled bank is not watched, not indexed and not searchable —
        # it must not look like an empty one (§6.1).
        raise ApiError("bank_not_found", f"bank {bank.name!r} is disabled", ref=ref)
    return bank


def _open_bank(bank: Bank):
    """Connection to a bank's index, or ``None`` when it has none yet.

    Never creates the file: listing banks must not leave an empty database
    behind for every root that was registered but never indexed.
    """
    if not bank.db_path.exists():
        return None
    return store.connect(bank.db_path)


def _status_for(bank: Bank, chunk_count: int) -> tuple[str, int]:
    """``indexing`` > ``empty`` > ``ready`` (§5.2).

    ``indexing`` wins over ``empty`` on purpose: ``empty`` tells an agent the
    bank is pointless and it stops asking; ``indexing`` with ``chunk_count=0``
    tells it to come back.
    """
    queued = _queued(bank.id)
    if queued > 0 or _busy(bank.id):
        return "indexing", queued
    if chunk_count == 0:
        return "empty", queued
    return "ready", queued


def _bank_info(bank: Bank) -> dict:
    """The one bank shape the API returns (§9.5)."""
    files = chunks = db_bytes = 0
    last_indexed: str | None = None
    conn = None
    try:
        conn = _open_bank(bank)
        if conn is not None:
            files = store.file_count(conn)
            chunks = store.chunk_count(conn)
            db_bytes = bank.db_path.stat().st_size
            last_indexed = store.get_meta(conn).get("last_indexed_at")
    except Exception as exc:  # noqa: BLE001 - a broken index must still list
        log.warning("cannot read index of bank %s: %s", bank.name, exc)
    finally:
        if conn is not None:
            conn.close()
    status, queued = _status_for(bank, chunks)
    return {
        "id": bank.id,
        "name": bank.name,
        "root": bank.root.as_posix(),
        "provider": bank.provider,
        "enabled": bank.enabled,
        "exists": bank.exists,
        "git": bank.is_git,
        "files": files,
        "chunks": chunks,
        "db_bytes": db_bytes,
        "last_indexed": last_indexed,
        "status": status,
        "queued": queued,
        "indexing": status == "indexing",
        "last_error": servicelog.last_index_error(bank.id),
    }


def _hit_json(hit: Any) -> dict:
    span = getattr(hit, "span", None)
    return {
        "chunk_uid": getattr(hit, "chunk_uid", None),
        "path": getattr(hit, "path", None),
        "heading": getattr(hit, "heading", None),
        "chunk_index": getattr(hit, "chunk_index", None),
        "span": list(span) if span else None,
        "score": getattr(hit, "score", None),
        "sim": getattr(hit, "sim", None),
        "content": getattr(hit, "content", None),
    }


def _parse_time(value: str | None) -> float | None:
    """ISO-8601 or epoch seconds — both accepted for ``since`` / ``until``."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ApiError("bad_request", f"cannot parse time {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


# ------------------------------------------------------- exclude matching


def _glob_re(pattern: str) -> re.Pattern[str]:
    """Translate one ``.gitignore``-ish glob to a regex over POSIX relpaths.

    Only what the registry's ``exclude`` needs: ``**/`` (any depth), ``**``,
    ``*`` and ``?`` (never crossing ``/``).
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _compile_excludes(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [_glob_re(p) for p in patterns]


def _excluded(rel: str, patterns: list[re.Pattern[str]], *, is_dir: bool) -> bool:
    if any(p.match(rel) for p in patterns):
        return True
    # ".git/**" excludes the contents of .git, which in practice means the
    # directory itself is not worth descending into.
    return is_dir and any(p.match(f"{rel}/_") for p in patterns)


# -------------------------------------------------------------- websocket


class Hub:
    """Fan-out for service events (§9.7).

    Phase 3's producers run on worker threads, so ``publish`` is thread-safe
    and hands the envelope to the event loop; nothing here blocks the worker.
    """

    def __init__(self) -> None:
        self._clients: dict[WebSocket, str | None] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)

    async def connect(self, ws: WebSocket, bank_id: str | None) -> None:
        await ws.accept()
        with self._lock:
            self._clients[ws] = bank_id

    def disconnect(self, ws: WebSocket) -> None:
        with self._lock:
            self._clients.pop(ws, None)

    def subscribe(self, ws: WebSocket, bank_id: str | None) -> None:
        with self._lock:
            if ws in self._clients:
                self._clients[ws] = bank_id

    def envelope(self, type_: str, data: dict, bank_id: str | None) -> dict:
        return {
            "v": 1,
            "type": type_,
            "ts": _now_iso(),
            "bank_id": bank_id,
            "data": data,
        }

    async def send(self, ws: WebSocket, envelope: dict) -> None:
        with suppress(Exception):
            await ws.send_json(envelope)

    async def broadcast(self, envelope: dict) -> None:
        bank_id = envelope.get("bank_id")
        with self._lock:
            targets = [
                ws
                for ws, filt in self._clients.items()
                if filt is None or bank_id is None or filt == bank_id
            ]
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(envelope)
            except Exception:  # noqa: BLE001 - a dropped client is not an error
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def publish(self, type_: str, data: dict, bank_id: str | None = None) -> None:
        """Thread-safe entry point for producers outside the event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        envelope = self.envelope(type_, data, bank_id)
        with suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(self.broadcast(envelope), loop)


hub = Hub()


def _queue_snapshot_json() -> dict:
    q = _queue()
    if q is None:
        return {"depth": 0, "high": 0, "normal": 0, "low": 0, "current": None}
    try:
        snap = q.snapshot()
    except Exception:  # noqa: BLE001
        return {"depth": 0, "high": 0, "normal": 0, "low": 0, "current": None}
    current = getattr(snap, "current", None)
    return {
        "depth": snap.depth,
        "high": snap.high,
        "normal": snap.normal,
        "low": snap.low,
        "current": None
        if current is None
        else {
            "task_id": current.id,
            "bank_id": current.bank_id,
            "kind": current.kind,
            "path": current.path,
            "batch": getattr(snap, "current_batch", 0),
            "batches": getattr(snap, "current_batches", 0),
        },
    }


# -------------------------------------------------------------- lifecycle


def _write_service_info() -> None:
    """``service.json`` — how ``service_ctl`` finds a running backend (§11.2)."""
    import sys

    info = {
        "pid": os.getpid(),
        "port": API_PORT,
        "host": API_HOST,
        "started_at": _started_iso,
        "version": SERVICE_VERSION,
        "python": Path(sys.executable).as_posix(),
    }
    with suppress(OSError):
        SERVICE_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
        SERVICE_INFO_FILE.write_text(
            json.dumps(info, indent=2) + "\n", encoding="utf-8"
        )


def _reconcile_on_start() -> None:
    """Catch up on whatever changed while the service was down (§9.6 step 4)."""
    if str(os.environ.get("MNEMO_RECONCILE_ON_START", "1")).strip() == "0":
        return
    q = _queue()
    if q is None:
        log.info("reconcile-on-start skipped: no work queue yet (phase 3)")
        return
    for bank in registry.load():
        if not bank.enabled:
            continue
        rebuild = False
        if bank.db_path.exists():
            conn = None
            try:
                conn = store.connect(bank.db_path)
                provider = get_provider(bank.provider)
                rebuild = store.needs_rebuild(
                    conn, provider_key=provider.key, dim=provider.dim
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("cannot inspect bank %s: %s", bank.name, exc)
            finally:
                if conn is not None:
                    conn.close()
        with suppress(Exception):
            q.enqueue_bulk(bank.id, trigger="startup", rebuild=rebuild)


async def _ping_loop() -> None:
    while True:
        await asyncio.sleep(WS_PING_INTERVAL_S)
        await hub.broadcast(hub.envelope("ping", {}, None))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start order is §9.6 — token, journal, registry, catch-up, queue,
    # watcher, then announce ourselves.
    api_token()
    servicelog.connect()
    servicelog.start_pruner()
    registry.load(force=True)
    _reconcile_on_start()

    q = _queue()
    if q is not None:
        with suppress(Exception):
            q.start(
                workers=int(os.environ.get("MNEMO_WORKERS", "1")),
                on_event=lambda ev: hub.publish(
                    ev.get("type", "queue"), ev.get("data", {}), ev.get("bank_id")
                ),
            )
    try:
        from . import watcher as _watcher  # noqa: PLC0415
    except ImportError:
        _watcher = None
    if _watcher is not None:
        with suppress(Exception):
            _watcher.start()

    hub.bind(asyncio.get_running_loop())
    ping = asyncio.create_task(_ping_loop())
    _write_service_info()
    log.info("mnemo backend %s on %s:%s", SERVICE_VERSION, API_HOST, API_PORT)
    try:
        yield
    finally:
        ping.cancel()
        with suppress(asyncio.CancelledError):
            await ping
        hub.bind(None)
        if _watcher is not None:
            with suppress(Exception):
                _watcher.stop()
        if q is not None:
            with suppress(Exception):
                q.stop()
        servicelog.stop_pruner()
        servicelog.close()
        with suppress(OSError):
            SERVICE_INFO_FILE.unlink()


app = FastAPI(
    title="mnemo",
    version=SERVICE_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)


# ------------------------------------------------------- auth + envelope


_GUARDED = ("/api", "/mcp")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith(_GUARDED):
        header = request.headers.get("authorization", "")
        presented = (
            header[7:] if header.lower().startswith("bearer ") else None
        ) or request.headers.get("x-mnemo-token")
        if not _token_ok(presented):
            return JSONResponse(
                _envelope("unauthorized", "missing or invalid API token"),
                status_code=401,
            )
    return await call_next(request)


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        _envelope(exc.code, exc.message, exc.detail), status_code=exc.status
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        _envelope("validation_error", "request body failed validation",
                  json.loads(json.dumps(exc.errors(), default=str))),
        status_code=422,
    )


@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    # Framework-level failures (unknown route, wrong method). Domain 404s go
    # through ApiError with a precise code; these are just bad requests.
    code = {401: "unauthorized", 405: "bad_request", 404: "bad_request"}.get(
        exc.status_code, "internal"
    )
    return JSONResponse(
        _envelope(code, str(exc.detail)), status_code=exc.status_code
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(_envelope("internal", str(exc)), status_code=500)


# --------------------------------------------------------------- schemas


class SearchRequest(BaseModel):
    bank: str
    query: str
    top_k: int = Field(default=TOP_K, ge=1, le=50)
    path_prefix: str | None = None
    expand_window: int | None = Field(default=None, ge=0, le=10)
    face: str = "http"


class AddBankRequest(BaseModel):
    root: str
    name: str | None = None
    provider: str | None = None


class ReindexRequest(BaseModel):
    bank: str
    path: str | None = None
    full: bool = False


# -------------------------------------------------------------- endpoints


@app.get("/health")
def health() -> dict:
    """Liveness — deliberately token-free (§9.1)."""
    try:
        provider = get_provider()
        embed = {
            "provider": provider.name,
            "reachable": bool(provider.health()),
            "host": getattr(config, "EMBED_HOST", None),
            "port": getattr(config, "EMBED_PORT", None),
        }
    except Exception as exc:  # noqa: BLE001 - health never fails
        embed = {"provider": None, "reachable": False, "error": str(exc)}
    try:
        banks = len(registry.load())
    except Exception:  # noqa: BLE001
        banks = 0
    return {
        "ok": True,
        "version": SERVICE_VERSION,
        "pid": os.getpid(),
        "port": API_PORT,
        "uptime_s": round(time.time() - _started_at, 1),
        "banks": banks,
        "queue_depth": _queued(),
        "embed": embed,
    }


def _engine_search(conn, req: SearchRequest, bank: Bank) -> list[Any]:
    """Call ``search.search`` on the v3 contract (§5).

    Imported here, not at module import: ``search`` pulls the embedder, and
    the backend must not drag ONNX into its own process just to start.
    The signature check is the seam onto engine-dev — if ``search.py`` is
    still the v2 shape we say so, loudly, instead of inventing results.
    """
    from . import search as search_mod  # noqa: PLC0415

    params = inspect.signature(search_mod.search).parameters
    if "conn" not in params:
        raise ApiError(
            "internal",
            "src/search.py still has the v2 signature (no `conn`); "
            "the v3 search contract (§5) is not in place yet",
        )
    return search_mod.search(
        conn,
        req.query,
        provider=get_provider(bank.provider),
        top_k=req.top_k,
        path_prefix=req.path_prefix,
        expand_window=req.expand_window,
    )


@app.post("/api/search")
def api_search(req: SearchRequest) -> dict:
    bank = _resolve_bank(req.bank)
    started = time.perf_counter()
    conn = _open_bank(bank)
    degraded: str | None = None
    hits: list[Any] = []
    chunks = 0
    try:
        if conn is not None:
            chunks = store.chunk_count(conn)
        status, queued = _status_for(bank, chunks)
        # No chunks means no possible match — never load a model to prove it.
        if chunks:
            try:
                hits = _engine_search(conn, req, bank)
            except EmbeddingUnavailable as exc:
                # NFR-10: degrade, do not fail. The caller still learns the
                # bank's real state and that the answer is incomplete.
                degraded = "embed_unavailable"
                log.warning("search degraded on bank %s: %s", bank.name, exc)
    finally:
        if conn is not None:
            conn.close()
    took_ms = (time.perf_counter() - started) * 1000

    servicelog.log_query(
        bank_id=bank.id,
        face=req.face,
        query=req.query,
        path_prefix=req.path_prefix,
        status=status,
        hits=hits,
        took_ms=took_ms,
    )
    hub.publish(
        "query",
        {
            "face": req.face,
            "query": req.query,
            "status": status,
            "n_hits": len(hits),
            "took_ms": round(took_ms, 1),
        },
        bank.id,
    )
    body = {
        "bank_id": bank.id,
        "bank_name": bank.name,
        "query": req.query,
        "status": status,
        "queued": queued,
        "chunk_count": chunks,
        "took_ms": round(took_ms, 1),
        "hits": [_hit_json(h) for h in hits],
    }
    if degraded:
        body["degraded"] = degraded
    return body


@app.get("/api/banks")
def api_banks() -> dict:
    return {"banks": [_bank_info(b) for b in registry.load()]}


@app.post("/api/banks", status_code=201)
def api_add_bank(req: AddBankRequest) -> dict:
    try:
        bank = registry.add(req.root, name=req.name, provider=req.provider)
    except NotADirectoryError as exc:
        raise ApiError("root_not_found", str(exc), root=req.root) from exc
    except BankExists as exc:
        raise ApiError("bank_exists", str(exc), root=req.root) from exc
    info = _bank_info(bank)
    hub.publish("bank_added", {"bank": info}, bank.id)
    q = _queue()
    if q is not None:
        with suppress(Exception):
            q.enqueue_bulk(bank.id, trigger="api")
    else:
        log.info("bank %s registered; indexing waits for the queue", bank.name)
    return info


@app.get("/api/banks/{bank_id}")
def api_bank(bank_id: str) -> dict:
    return _bank_info(_resolve_bank(bank_id, require_enabled=False))


@app.delete("/api/banks/{bank_id}")
def api_remove_bank(bank_id: str, drop_index: bool = True) -> dict:
    bank = _resolve_bank(bank_id, require_enabled=False)
    registry.remove(bank.id, drop_index=drop_index)
    hub.publish("bank_removed", {"bank_id": bank.id}, bank.id)
    return {"ok": True}


@app.post("/api/reindex", status_code=202)
def api_reindex(req: ReindexRequest) -> dict:
    bank = _resolve_bank(req.bank)
    q = _require_queue()
    if req.path:
        rel = _safe_relpath(bank, req.path)
        task_ids = [
            q.enqueue_file(bank.id, rel, priority=q.Priority.NORMAL, trigger="api")
        ]
    else:
        task_ids = [q.enqueue_bulk(bank.id, trigger="api", rebuild=req.full)]
    return {"ok": True, "task_ids": task_ids, "queued": _queued(bank.id)}


def _safe_relpath(bank: Bank, path: str) -> str:
    """Bank-relative POSIX path, or ``path_outside_bank``."""
    candidate = Path(path)
    root = bank.root.expanduser().resolve()
    absolute = (root / candidate) if not candidate.is_absolute() else candidate
    try:
        rel = absolute.resolve().relative_to(root)
    except ValueError as exc:
        raise ApiError("path_outside_bank", f"{path!r} is outside {bank.name}",
                       path=path) from exc
    return rel.as_posix()


@app.get("/api/tree")
def api_tree(
    bank: str,
    links: bool = False,
    depth: int = 0,
) -> dict:
    b = _resolve_bank(bank, require_enabled=False)
    root = b.root.expanduser().resolve()
    if not root.is_dir():
        raise ApiError("root_not_found", f"{root.as_posix()} is gone", ref=bank)

    indexed: dict[str, dict] = {}
    conn = _open_bank(b)
    try:
        if conn is not None:
            for row in conn.execute(
                "SELECT path, heading FROM chunks ORDER BY path, chunk_index"
            ):
                entry = indexed.setdefault(
                    row["path"], {"chunks": 0, "headings": []}
                )
                entry["chunks"] += 1
                heading = row["heading"]
                if heading and heading not in entry["headings"]:
                    entry["headings"].append(heading)
    finally:
        if conn is not None:
            conn.close()

    patterns = _compile_excludes(b.exclude)
    files = dirs = 0
    tree: dict[str, Any] = {"name": "", "type": "dir", "path": "", "children": []}
    nodes: dict[str, dict] = {"": tree}

    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        level = 0 if not rel_dir else rel_dir.count("/") + 1
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not _excluded(
                f"{rel_dir}/{d}" if rel_dir else d, patterns, is_dir=True
            )
        )
        if depth and level >= depth:
            dirnames[:] = []
        parent = nodes.get(rel_dir)
        if parent is None:
            continue
        for name in dirnames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            child: dict[str, Any] = {
                "name": name, "type": "dir", "path": rel, "children": [],
            }
            nodes[rel] = child
            parent["children"].append(child)
            dirs += 1
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if _excluded(rel, patterns, is_dir=False):
                continue
            info = indexed.get(rel)
            leaf: dict[str, Any] = {
                "name": name,
                "type": "file",
                "path": rel,
                "size": (here / name).stat().st_size,
                "indexed": info is not None,
                "chunks": info["chunks"] if info else 0,
                "headings": info["headings"] if info else [],
            }
            if links:
                leaf["links"] = _md_links(here / name)
            parent["children"].append(leaf)
            files += 1

    # Directories before files, each group by name (§9.5).
    for node in nodes.values():
        node["children"].sort(key=lambda c: (c["type"] != "dir", c["name"]))

    return {
        "bank_id": b.id,
        "root": root.as_posix(),
        "files": files,
        "dirs": dirs,
        "tree": tree,
    }


_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+\.md)[^)]*\)")


def _md_links(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen: list[str] = []
    for match in _MD_LINK.finditer(text):
        target = match.group(1)
        if target not in seen:
            seen.append(target)
    return seen


def _as_indexed(raw: bytes) -> str:
    """Decode a ``.md`` exactly the way the indexer read it.

    ``start_char`` / ``end_char`` are **Python code-point offsets** into this
    string — not bytes, not UTF-16 units. Two conversions have to match the
    indexer's ``Path.read_text(encoding="utf-8")`` or every boundary the
    cabinet draws is silently wrong:

    * **UTF-8 decode**, so one Cyrillic letter is one position, not two;
    * **universal newlines**, so a CRLF file (the norm on Windows) counts one
      position per line break. Skipping this shifts every chunk after the
      first line by one, and the drawing still *looks* plausible.

    The invariant callers may rely on: ``text[start_char:end_char]`` equals
    the indexed chunk under plain Python slicing.
    """
    return raw.decode("utf-8", errors="replace").replace(
        "\r\n", "\n"
    ).replace("\r", "\n")


@app.get("/api/file")
def api_file(bank: str, path: str) -> dict:
    b = _resolve_bank(bank, require_enabled=False)
    rel = _safe_relpath(b, path)
    if not rel.endswith(".md"):
        raise ApiError("bad_request", "only .md files are served", path=path)
    absolute = b.root.expanduser().resolve() / rel
    if not absolute.is_file():
        raise ApiError("file_not_found", f"{rel} is not in {b.name}", path=rel)
    size = absolute.stat().st_size
    if size > FILE_MAX_BYTES:
        raise ApiError(
            "bad_request",
            f"{rel} is {size} bytes, over the {FILE_MAX_BYTES} limit",
            path=rel,
        )
    raw = absolute.read_bytes()
    text = _as_indexed(raw)

    chunks: list[dict] = []
    indexed = False
    conn = _open_bank(b)
    try:
        if conn is not None:
            rows = store.chunk_map(conn, rel)
            indexed = store.get_file_row(conn, rel) is not None
            chunks = [
                {
                    "chunk_uid": r["chunk_uid"],
                    "chunk_index": r["chunk_index"],
                    "heading": r["heading"],
                    "start_char": r["start_char"],
                    "end_char": r["end_char"],
                }
                for r in rows
            ]
    finally:
        if conn is not None:
            conn.close()

    return {
        "bank_id": b.id,
        "path": rel,
        "size": size,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "indexed": indexed,
        "text": text,
        "chunks": chunks,
    }


@app.get("/api/status")
def api_status() -> dict:
    try:
        provider = get_provider()
        embed = {
            "reachable": bool(provider.health()),
            "host": getattr(config, "EMBED_HOST", None),
            "port": getattr(config, "EMBED_PORT", None),
        }
        provider_name = provider.name
    except Exception as exc:  # noqa: BLE001
        embed = {"reachable": False, "error": str(exc)}
        provider_name = None
    return {
        "service": {
            "version": SERVICE_VERSION,
            "pid": os.getpid(),
            "port": API_PORT,
            "started_at": _started_iso,
            "uptime_s": round(time.time() - _started_at, 1),
            "provider": provider_name,
            "priority_enabled": os.environ.get("MNEMO_QUEUE_PRIORITY", "1") != "0",
            "embed": embed,
        },
        "queue": _queue_snapshot_json(),
        "banks": [_bank_info(b) for b in registry.load()],
    }


@app.get("/api/logs")
def api_logs(
    kind: Literal["query", "index"],
    bank: str | None = None,
    since: str | None = None,
    until: str | None = None,
    event_kind: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    bank_id = _resolve_bank(bank, require_enabled=False).id if bank else None
    t_since, t_until = _parse_time(since), _parse_time(until)
    if kind == "query":
        events = servicelog.read_queries(
            bank_id=bank_id, since=t_since, until=t_until,
            limit=limit, offset=offset,
        )
        total = servicelog.count(
            "query", bank_id=bank_id, since=t_since, until=t_until
        )
    else:
        events = servicelog.read_index(
            bank_id=bank_id, since=t_since, until=t_until, kind=event_kind,
            limit=limit, offset=offset,
        )
        total = servicelog.count(
            "index", bank_id=bank_id, since=t_since, until=t_until,
            kind=event_kind,
        )
    return {
        "kind": kind,
        "total": total,
        "limit": limit,
        "offset": offset,
        "events": events,
    }


# -------------------------------------------------------------- websocket


@app.websocket("/ws")
async def ws_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    bank: str | None = Query(default=None),
) -> None:
    """Live service events. The token rides in the query string because a
    browser cannot set headers on a WebSocket handshake (§9.1)."""
    if not _token_ok(token):
        await websocket.close(code=1008)
        return
    await hub.connect(websocket, bank)
    try:
        await hub.send(
            websocket,
            hub.envelope(
                "hello",
                {
                    "version": SERVICE_VERSION,
                    "banks": [b.id for b in registry.load()],
                    "queue": _queue_snapshot_json(),
                },
                None,
            ),
        )
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue  # clients must tolerate us ignoring noise, and vice versa
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "subscribe":
                hub.subscribe(websocket, msg.get("bank_id"))
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(websocket)


# The cabinet's assets. Mounted only when ui-dev has shipped them, so the
# backend runs headless until then.
if WEBUI_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=WEBUI_DIR, html=True), name="ui")


def run(host: str | None = None, port: int | None = None) -> None:
    """Serve. ``mnemo service start`` (phase 5) calls this in a child."""
    import uvicorn

    uvicorn.run(
        app,
        host=host or API_HOST,
        port=port or API_PORT,
        log_level=os.environ.get("MNEMO_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
