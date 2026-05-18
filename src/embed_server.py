"""Warm embedding helper: a light, local, cross-platform resident.

Why this exists: a ``UserPromptSubmit`` hook spawns a fresh process per
user message; loading e5-large each time costs ~3 s + ~1.6 GB. This keeps
one model resident per machine and answers query embeddings over loopback
TCP in milliseconds.

Sanctioned exception to the Memory-design-v2 "no daemon" anti-pattern:
light, local, user-scoped, loopback-only, auto-started, idle-exiting,
nothing in git, nothing to install. The model is still never downloaded
implicitly — ``warmup`` stays explicit; the resident only holds an already
downloaded model.

NOT a unix socket: CPython does not expose ``socket.AF_UNIX`` on Windows.
Loopback TCP (127.0.0.1) + a token file behaves identically on
Linux / macOS / Windows.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

from .config import (
    EMBED_HOST,
    EMBED_IDLE_TIMEOUT,
    EMBED_PORT,
    EMBED_TOKEN_FILE,
)

_HDR = struct.Struct("!I")  # 4-byte length prefix


def _recv_n(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed")
        buf.extend(chunk)
    return bytes(buf)


def _send(sock: socket.socket, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(_HDR.pack(len(data)) + data)


def _recv(sock: socket.socket, timeout: float) -> dict:
    sock.settimeout(timeout)
    (n,) = _HDR.unpack(_recv_n(sock, _HDR.size))
    return json.loads(_recv_n(sock, n).decode("utf-8"))


def _token() -> str:
    """Read the shared secret, creating it (0600) on first server start."""
    if EMBED_TOKEN_FILE.exists():
        return EMBED_TOKEN_FILE.read_text().strip()
    EMBED_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    tok = os.urandom(24).hex()
    EMBED_TOKEN_FILE.write_text(tok)
    try:
        os.chmod(EMBED_TOKEN_FILE, 0o600)  # best-effort; no-op on some FS
    except OSError:
        pass
    return tok


# --------------------------------------------------------------- server


def serve() -> None:
    """Resident model holder. Binds loopback, serves embeddings, exits on
    idle. Singleton: if the port is already taken another instance won —
    exit quietly."""
    tok = _token()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((EMBED_HOST, EMBED_PORT))
    except OSError:
        return  # another resident already owns the port
    srv.listen(8)
    srv.settimeout(EMBED_IDLE_TIMEOUT)

    # Lazy: the model loads on the first request (the documented ~3 s cost,
    # paid once per cold start, not per message).
    from .embedder import embed_passages, embed_query

    while True:
        try:
            conn, _ = srv.accept()
        except socket.timeout:
            return  # idle long enough -> free the ~1.6 GB
        with conn:
            try:
                req = _recv(conn, timeout=10.0)
                if req.get("token") != tok:
                    _send(conn, {"error": "unauthorized"})
                    continue
                if req.get("kind") == "passages":
                    texts = req.get("texts")
                    if (
                        not isinstance(texts, list)
                        or not texts
                        or not all(
                            isinstance(t, str) and t.strip() for t in texts
                        )
                    ):
                        _send(conn, {"error": "empty"})
                        continue
                    _send(conn, {"vecs": embed_passages(texts)})
                    continue
                text = (req.get("text") or "").strip()
                if not text:
                    _send(conn, {"error": "empty"})
                    continue
                if req.get("kind") == "passage":
                    vec = embed_passages([text])[0]
                else:
                    vec = embed_query(text)
                _send(conn, {"vec": vec})
            except (OSError, ValueError, json.JSONDecodeError):
                pass  # one bad client must never kill the resident


# --------------------------------------------------------------- client


def _connect(timeout: float) -> socket.socket | None:
    try:
        return socket.create_connection((EMBED_HOST, EMBED_PORT), timeout=timeout)
    except OSError:
        return None


def _spawn_server() -> None:
    """Start the resident detached — cross-platform."""
    engine_root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, "-m", "src.cli", "embed-server"]
    kwargs: dict = dict(
        cwd=str(engine_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
    except OSError:
        pass


def _obtain_socket() -> socket.socket | None:
    """Connect to the resident, spawning + waiting for it on a cold start.

    Returns None if the resident cannot be reached — every caller must
    treat that as "degrade gracefully", never as fatal.
    """
    sock = _connect(timeout=0.5)
    if sock is not None:
        return sock
    _spawn_server()
    deadline = time.time() + 5.0  # process bind is fast (no model yet)
    while time.time() < deadline:
        time.sleep(0.2)
        sock = _connect(timeout=0.5)
        if sock is not None:
            return sock
    return None


def embed_query_via_server(text: str) -> list[float] | None:
    """Query embedding from the resident, starting it if needed.

    Returns None on ANY failure — the caller skips injection and never
    blocks the user's turn.
    """
    if not text.strip():
        return None
    try:
        tok = _token()
    except OSError:
        return None

    sock = _obtain_socket()
    if sock is None:
        return None
    try:
        with sock:
            _send(sock, {"token": tok, "text": text, "kind": "query"})
            # Generous: the first request after a cold start loads the
            # model (~3 s). Steady state is milliseconds.
            resp = _recv(sock, timeout=20.0)
        vec = resp.get("vec")
        return vec if isinstance(vec, list) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def embed_passages_via_server(texts: list[str]) -> list[list[float]] | None:
    """Batch document embeddings from the resident, starting it if needed.

    The indexing path (``reindex``) calls this so that neither the
    short-lived PostToolUse hook nor the long-lived MCP process ever
    loads the ~2.2 GB model itself — that in-process load was the cause
    of both the per-edit CPU storm and the multi-GB MCP growth.

    Returns None on ANY failure so the caller can fall back to an
    in-process embed (keeps ``mnemo ingest`` / tests deterministic when
    the resident is genuinely unavailable, e.g. offline / CI).
    """
    if not texts or not all(isinstance(t, str) and t.strip() for t in texts):
        return None
    try:
        tok = _token()
    except OSError:
        return None

    sock = _obtain_socket()
    if sock is None:
        return None
    try:
        with sock:
            _send(sock, {"token": tok, "texts": texts, "kind": "passages"})
            # Cold start loads the model (~3 s) then embeds the batch;
            # steady state is milliseconds. Generous ceiling.
            resp = _recv(sock, timeout=60.0)
        vecs = resp.get("vecs")
        if not isinstance(vecs, list) or len(vecs) != len(texts):
            return None
        return vecs
    except (OSError, ValueError, json.JSONDecodeError):
        return None
