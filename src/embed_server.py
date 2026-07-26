"""Warm embedding helper: a light, local, cross-platform resident.

Why this exists: a ``UserPromptSubmit`` hook spawns a fresh process per
user message; loading e5-large each time costs ~3 s + ~1.6 GB. This keeps
one model resident per machine and answers query embeddings over loopback
TCP in milliseconds.

Light, local, user-scoped, auto-started, idle-exiting; nothing in git,
nothing to install. The model is still never downloaded implicitly —
``warmup`` stays explicit; the resident only holds an already downloaded
model. Base mode is loopback-only; the bind and dial addresses are
configurable so isolated environments on one machine can share a single
resident (see ``config.EMBED_BIND`` / ``EMBED_HOST``).

NOT a unix socket: CPython does not expose ``socket.AF_UNIX`` on Windows.
TCP + a token file behaves identically on Linux / macOS / Windows.
"""
from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

from .config import (
    EMBED_BATCH_TIMEOUT_FLOOR,
    EMBED_BIND,
    EMBED_HOST,
    EMBED_HOST_IS_LOCAL,
    EMBED_IDLE_TIMEOUT,
    EMBED_PORT,
    EMBED_PROBE_TIMEOUT,
    EMBED_SECONDS_PER_CHUNK,
    EMBED_TOKEN_FILE,
    LEGACY_EMBED_TOKEN_FILE,
)

_HDR = struct.Struct("!I")  # 4-byte length prefix


class TokenRejected(RuntimeError):
    """The resident is alive and refused our token.

    A distinct type because it needs a distinct response. "Unreachable" means
    fall back and embed locally; "refused" means we are talking to a resident
    that is not ours, and falling back would quietly load a second copy of the
    model — the failure mode this exception exists to make impossible.
    """


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


def token_file() -> Path:
    """Where the shared secret lives, resolved per call.

    Not frozen at import: a test or a container sets
    ``MNEMO_EMBED_TOKEN_FILE`` at runtime, and a constant captured when the
    module first loaded would quietly ignore it — the same import-time freeze
    that made the token follow ``STATE_DIR`` in the first place.
    """
    override = os.environ.get("MNEMO_EMBED_TOKEN_FILE")
    return Path(override) if override else EMBED_TOKEN_FILE


def _token() -> str:
    """Read the machine's shared secret, creating it (0600) on first use.

    On first run after the move out of STATE_DIR, adopt the token from the
    old default location if it is there: a resident may already be running
    with it, and minting a new one would make this client's very next call
    fail against a resident that is otherwise perfectly healthy.
    """
    path = token_file()
    if path.exists():
        return path.read_text().strip()

    tok = ""
    if LEGACY_EMBED_TOKEN_FILE.exists():
        try:
            tok = LEGACY_EMBED_TOKEN_FILE.read_text().strip()
        except OSError:
            tok = ""
    tok = tok or os.urandom(24).hex()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tok)
    try:
        os.chmod(path, 0o600)  # best-effort; no-op on some FS
    except OSError:
        pass
    return tok


# --------------------------------------------------------------- server


def serve() -> None:
    """Resident model holder. Binds $MNEMO_EMBED_BIND, serves embeddings,
    exits on idle. Singleton: if the port is already held, this instance
    steps aside — but says so on stderr, because a resident bound to a
    narrower address (e.g. a loopback one that autostarted first) silently
    shadows a wanted shared bind and cuts every remote client off.

    Refuses to start without a cached model. The resident is autostarted by
    clients (a search, a hook), and its first request would call
    ``TextEmbedding(...)``, which *downloads* ~2.2 GB when the model is
    absent. That would put an implicit download behind an ordinary search —
    the one thing "the model is fetched only by an explicit `warmup`" forbids.
    The in-process legs check ``is_model_cached()`` already; this closes the
    same hole on the spawn path."""
    from .embedder import is_model_cached

    if not is_model_cached():
        print(
            "mnemo embed-server: no model in the cache — refusing to start. "
            "Run `mnemo warmup` once (the only step allowed to download it).",
            file=sys.stderr,
        )
        return
    tok = _token()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((EMBED_BIND, EMBED_PORT))
    except OSError as e:
        print(
            f"mnemo embed-server: cannot bind {EMBED_BIND}:{EMBED_PORT} "
            f"({e.strerror or e}); another resident already holds the port — "
            f"stepping aside. If you meant this one to serve remotely, stop "
            f"the other resident first.",
            file=sys.stderr,
        )
        return
    srv.listen(8)
    # 0 -> no idle exit: stay resident for the environments we serve.
    srv.settimeout(EMBED_IDLE_TIMEOUT or None)

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


def _connect(timeout: float | None = None) -> socket.socket | None:
    """Probe the resident. ``timeout=None`` uses the configured probe budget.

    Loopback gets the tight budget (a live resident accepts in ~0.5 ms); a
    remote resident keeps the generous one, since the measurement that
    justified tightening it was taken on loopback only.
    """
    if timeout is None:
        timeout = EMBED_PROBE_TIMEOUT if EMBED_HOST_IS_LOCAL else 0.5
    try:
        return socket.create_connection((EMBED_HOST, EMBED_PORT), timeout=timeout)
    except OSError:
        return None


def _spawn_server() -> None:
    """Start the resident windowless and detached — cross-platform.

    Delegates to ``service_ctl.spawn_detached`` rather than assembling flags
    here: ``DETACHED_PROCESS`` alone is NOT enough on Windows (a child that
    calls ``AllocConsole()`` gets a real visible window), and MSDN says
    ``CREATE_NO_WINDOW`` is *ignored* when OR-ed with it — so the obvious
    "fix" of adding the flag would have degraded silently. One primitive,
    used by every spawn we make, is the only way NFR-1 stays true.

    Note the doc is wrong here: contracts §11.2 prescribes
    ``CREATE_NO_WINDOW | DETACHED_PROCESS``. docs-keeper is correcting it.
    """
    from .service_ctl import spawn_detached, windowless_python

    engine_root = Path(__file__).resolve().parent.parent
    # pythonw.exe is a GUI-subsystem binary: it cannot own a console at all,
    # which is the only form of the guarantee that survives being launched by
    # Task Scheduler or a shortcut.
    cmd = [windowless_python(), "-m", "src.cli", "embed-server"]
    try:
        spawn_detached(cmd, cwd=engine_root)
    except OSError:
        pass


def _obtain_socket() -> socket.socket | None:
    """Connect to the resident, spawning + waiting for it on a cold start.

    Only a resident on this machine's loopback is ours to start. When
    $MNEMO_EMBED_HOST names a remote one, we take it as-is: an unreachable
    remote must not make us load a second copy of the model right next to it.

    Nor do we spawn one that cannot serve. ``serve()`` refuses to start
    without a cached model, so on a model-less machine every call would
    otherwise launch a process, wait out the poll loop and still come back
    empty — measured at 7-20 s per search, repeated for every search, for a
    result that was never going to arrive. Checking the cache first turns
    that into one refused connection (~0.5 s) and an honest "unavailable".

    Returns None if the resident cannot be reached — every caller must
    treat that as "degrade gracefully", never as fatal.
    """
    sock = _connect()
    if sock is not None:
        return sock
    if not EMBED_HOST_IS_LOCAL:
        return None
    from .embedder import is_model_cached

    if not is_model_cached():
        return None  # nothing to serve; spawning would only burn wall clock
    _spawn_server()
    deadline = time.time() + 5.0  # process bind is fast (no model yet)
    while time.time() < deadline:
        time.sleep(0.2)
        sock = _connect()
        if sock is not None:
            return sock
    return None


def server_is_up() -> bool:
    """True if a resident is currently reachable — a quick TCP probe.

    Lets callers that would otherwise refuse for a missing *local* model
    proceed when embeddings can be offloaded to a live resident (e.g. a
    container that ships no model and dials a shared remote one). Does not
    autostart anything — a pure liveness check on the configured address.
    """
    sock = _connect()
    if sock is None:
        return False
    sock.close()
    return True


def _raise_if_unauthorized(resp: dict) -> None:
    """Turn the resident's refusal into a loud, specific failure.

    Raised rather than returned so no caller can absorb it by accident. The
    message names the fix, because the cause is almost always one process
    reading a different token file than the resident did.
    """
    if resp.get("error") != "unauthorized":
        return
    message = (
        f"the embedding resident at {EMBED_HOST}:{EMBED_PORT} refused our "
        f"token (read from {token_file()}). It is running with a "
        f"different secret — restart it, or point MNEMO_EMBED_TOKEN_FILE at "
        f"the one it used. NOT falling back to an in-process model: that "
        f"would load a second ~2.2 GB copy beside a working resident."
    )
    print(f"mnemo: {message}", file=sys.stderr)
    raise TokenRejected(message)


def embed_query_via_server(
    text: str, *, budget_s: float | None = None,
) -> list[float] | None:
    """Query embedding from the resident, starting it if needed.

    ``budget_s`` overrides the default recv timeout so callers with a
    wall-clock budget (hook-inject, MCP memory_search) can bound this
    step. ``None`` keeps the historical 20 s ceiling for batch / ingest
    paths where cold start (~3 s) must comfortably fit.

    Returns None when the resident cannot be reached — the caller skips
    injection and never blocks the user's turn. Raises ``TokenRejected`` when
    it *can* be reached and refuses us, because that is not a degradation to
    absorb silently.
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
            # Generous default: first request after a cold start loads the
            # model (~3 s). Steady state is milliseconds.
            resp = _recv(sock, timeout=budget_s if budget_s is not None else 20.0)
        _raise_if_unauthorized(resp)
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

    Returns None when the resident is genuinely unreachable, so the caller
    can fall back to an in-process embed (keeps ``mnemo ingest`` / tests
    deterministic offline / in CI). Raises ``TokenRejected`` when a live
    resident refuses us — falling back there would load a second copy of the
    model beside a perfectly good one and run 50x slower without a word.
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
    # Budget scales with the batch: this waits on a CPU-bound embed whose
    # duration depends on batch size and machine load, so a fixed ceiling is
    # the wrong shape. The old fixed 60 s was measured being crossed by a
    # 16-chunk batch at 4 threads (61.7 s) — a coin flip under load.
    budget = max(EMBED_BATCH_TIMEOUT_FLOOR, EMBED_SECONDS_PER_CHUNK * len(texts))
    try:
        with sock:
            _send(sock, {"token": tok, "texts": texts, "kind": "passages"})
            resp = _recv(sock, timeout=budget)
        _raise_if_unauthorized(resp)
        vecs = resp.get("vecs")
        if not isinstance(vecs, list) or len(vecs) != len(texts):
            return None
        return vecs
    except TimeoutError:
        # Giving up on a resident that is merely slow makes the caller load
        # its own ~2.2 GB copy. That used to happen in silence; say it.
        print(
            f"mnemo: the embedding resident did not answer within {budget:.0f}s "
            f"for a batch of {len(texts)} chunks. Falling back to an "
            f"in-process model, which is much slower and holds ~2.2 GB here. "
            f"Raise MNEMO_EMBED_SECONDS_PER_CHUNK if this recurs.",
            file=sys.stderr,
        )
        return None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
