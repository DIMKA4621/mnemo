"""The resident live-chat runtime (MN-43) — real ``claude`` CLI processes,
each attached to a real pseudoterminal, mirrored byte-in/byte-out over a
WebSocket. Owns exactly the same kind of thing `workqueue.py` owns for
indexing: a resident, in-process table (`_sessions`) that outlives any one
HTTP or WS request, started and stopped once by `api.py`'s
``lifespan()``/``_shutdown()``.

**Why a real PTY and not the Claude Agent SDK's structured events.** Decided
and confirmed live by two POCs before this module existed — see
`.claude/memory/topics/agents-feature.md` and the MN-43 ticket description.
The short version: a real binary in a real pty gives full, permanent
feature parity (slash commands, self-update, every future CLI feature) with
nothing here to keep in sync; the SDK's structured-event stream would need
a hand-written UI mapping for every one of those, forever.

**Key principle this module exists to hold.** The live process belongs to
the backend, not to any one browser tab. Closing a tab, or a client
reconnecting minutes later, must not touch the child process — only the set
of subscribers listening to it. Two things make that true:

* **Lazy spawn.** A chat *record* (`agent_registry.create_chat`) is free; a
  real ``claude`` process is a paid API call. So the process is spawned on
  the FIRST WebSocket subscriber for a ``chat_id``, never on chat creation.
* **No adoption after a restart.** `_sessions` is purely in-memory. A
  service restart empties it; the next connect spawns fresh. This is a
  deliberate simplification (MN-43's Jira decision comment, 2026-08-29),
  not an oversight — it sidesteps the PID-reuse hazard `v3-build.md` warns
  about entirely, because nothing here ever terminates a process by a PID
  read back off disk, only by a live handle held in this table. The
  trade-off is real and accepted for v1: `history.log` gives a visual
  replay of old output, but a freshly spawned ``claude`` does not remember
  the conversation (no ``--continue``) — a separate ticket if that turns
  out to matter.

**Race-free reconnect, in one sentence.** Every `Session` carries one lock
that guards two things together: appending new output to `history.log`, and
handing it to every live subscriber's queue. A new subscriber registers
itself in the subscriber set UNDER THAT SAME LOCK, at the same moment it
reads `history.log` for replay — so "where replay stops" and "where live
output starts" are the same instant from the producer's point of view, and
no byte can be lost or duplicated between them. See `_subscribe` and
`_publish_output`.

**Soft concurrency ceiling.** `config.MAX_LIVE_SESSIONS` (default 8,
machine-wide) is a cheap guard against a double-click or a UI bug forking a
pile of real, paid ``claude`` processes — not the tuned real policy (a
separate future ticket, MN-46, owns that). Exceeding it on a new spawn
raises `SessionLimitExceeded`.

**No console-hiding machinery here.** `v3-build.md`'s "no console windows
on Windows" rule is about GUI-subsystem spawns of mnemo's own service
(`pythonw` + `CREATE_NO_WINDOW`, via `service_ctl.spawn_detached`) — it does
not apply to PTY children. A PTY child legitimately needs a real
pseudoterminal (ConPTY on Windows, a POSIX pty elsewhere) by definition;
that is the entire point of this module, not something to hide.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import agent_registry, config

log = logging.getLogger("mnemo.agent_runtime")

_IS_WINDOWS = os.name == "nt"

if _IS_WINDOWS:
    from winpty import PtyProcess as _WinPtyProcess
else:
    # Deliberately NOT imported at module top-level unconditionally —
    # `ptyprocess` fails at `import fcntl` on Windows, so this module would
    # be unimportable there if the two branches were not kept separate.
    import ptyprocess as _ptyprocess  # noqa: PLC0415 - platform gate, not a hot path


class SessionLimitExceeded(RuntimeError):
    """The machine-wide live-session cap (`config.MAX_LIVE_SESSIONS`) is
    already reached."""


class ClaudeNotFound(RuntimeError):
    """No ``claude`` executable was found on ``PATH``."""


# ---------------------------------------------------------------- pty adapter


class _PtyHandle:
    """Unifies pywinpty (Windows/ConPTY) and ptyprocess (POSIX) behind one
    ``str``-in/``str``-out surface.

    pywinpty's `PtyProcess.read()` already returns `str` — it decodes UTF-8
    itself and, per its own source, reads one extra byte at a time to avoid
    splitting a multi-byte character across a chunk boundary. ptyprocess's
    `PtyProcess.read()` returns raw `bytes` with no such handling. The wire
    format this module promises (§43.3: "always UTF-8 text, even on the
    POSIX side") is what makes both platforms produce an identical envelope
    shape, so the POSIX branch decodes here, with ``errors="replace"`` — a
    chunk boundary that lands mid-character is possible in principle and
    would show a single replacement glyph rather than corrupt the stream or
    raise. This asymmetry is exactly what the docstring above calls out as
    the one part of this module not verified on this (Windows) machine.
    """

    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str],
                 dimensions: tuple[int, int]) -> None:
        if _IS_WINDOWS:
            self._proc = _WinPtyProcess.spawn(argv, cwd=cwd, env=env, dimensions=dimensions)
        else:
            self._proc = _ptyprocess.PtyProcess.spawn(
                argv, cwd=cwd, env=env, dimensions=dimensions
            )

    def read(self, size: int = 4096) -> str:
        """Blocks until data is available. Raises `EOFError` when the child
        (and its pty) has closed — the caller's cue to stop reading."""
        if _IS_WINDOWS:
            return self._proc.read(size)
        data: bytes = self._proc.read(size)
        return data.decode("utf-8", errors="replace")

    def write(self, data: str) -> None:
        if _IS_WINDOWS:
            self._proc.write(data)
        else:
            self._proc.write(data.encode("utf-8"))

    def setwinsize(self, rows: int, cols: int) -> None:
        self._proc.setwinsize(rows, cols)

    def isalive(self) -> bool:
        try:
            return bool(self._proc.isalive())
        except Exception:  # noqa: BLE001 - a broken handle is not alive
            return False

    def terminate(self, *, force: bool = False) -> None:
        self._proc.terminate(force=force)

    @property
    def exitstatus(self) -> int | None:
        return self._proc.exitstatus


# ------------------------------------------------------------------- session


@dataclass
class Session:
    """One resident live chat. Exactly one per ``chat_id`` at a time — see
    `_sessions`."""

    chat_id: str
    agent_slug: str
    history_path: Path
    proc: _PtyHandle
    # Guards `history_path`'s append handle and `subscribers` together — see
    # the module docstring's "race-free reconnect" note. Never held across a
    # blocking PTY read; only around the short append+fan-out in
    # `_publish_output` and around subscribe/unsubscribe.
    lock: threading.Lock = field(default_factory=threading.Lock)
    subscribers: dict[str, "asyncio.Queue[dict[str, Any]]"] = field(default_factory=dict)
    reader_thread: threading.Thread | None = None
    alive: bool = True
    exit_code: int | None = None
    created_at: float = field(default_factory=time.time)
    _history_fh: Any = field(default=None, repr=False)


_sessions: dict[str, Session] = {}
_sessions_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Called once from `api.py`'s `lifespan()` (mirrors `Hub.bind`) so
    reader threads — which run outside the event loop — have somewhere
    thread-safe to deliver output. `None` on shutdown, same as `Hub.bind`."""
    global _loop
    _loop = loop


def live_session_count() -> int:
    with _sessions_lock:
        return sum(1 for s in _sessions.values() if s.alive)


# ------------------------------------------------------- launch translation
#
# The launch-mode -> real invocation mapping (MN-43's Jira decision comment,
# 2026-08-29, final — not re-derived here):
#
#   mode "standard"  -> `claude`, no special env or flags beyond cwd.
#   mode "custom"    -> ANTHROPIC_BASE_URL=http://{host}:{port} (the one
#                       documented mechanism to point the Claude Code CLI at
#                       a proxy); `--model <value>` appended when `model` is
#                       set; `extra_args` appended verbatim when set.
#                       `autocompact` is skipped — no known flag or env var
#                       for it in the documented CLI interface, and this is
#                       not the place to invent one.


def _claude_executable() -> str:
    path = shutil.which("claude")
    if not path:
        raise ClaudeNotFound("no 'claude' executable found on PATH")
    return path


def _build_argv(launch: dict[str, Any]) -> list[str]:
    argv = [_claude_executable()]
    if launch.get("mode") == "custom":
        model = launch.get("model")
        if model:
            argv += ["--model", str(model)]
        for arg in launch.get("extra_args") or ():
            argv.append(str(arg))
    return argv


def _build_env(launch: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    if launch.get("mode") == "custom":
        env["ANTHROPIC_BASE_URL"] = f"http://{launch['host']}:{launch['port']}"
    return env


# --------------------------------------------------------------- publishing


def _safe_put(queue: "asyncio.Queue[dict[str, Any]]", item: dict[str, Any]) -> None:
    with suppress(Exception):
        queue.put_nowait(item)


def _deliver(subs: list["asyncio.Queue[dict[str, Any]]"], item: dict[str, Any]) -> None:
    """Cross-thread delivery to every live subscriber's queue — called from
    a reader thread, never from the event loop itself. Same
    `call_soon_threadsafe` technique `Hub.publish` uses, kept independent of
    `Hub` on purpose: this is a per-chat broadcast domain, not the
    service-wide event bus (§43.3 — "don't import Hub itself")."""
    loop = _loop
    if loop is None or loop.is_closed():
        return
    for queue in subs:
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(_safe_put, queue, item)


def _publish_output(session: Session, chunk: str) -> None:
    with session.lock:
        if session._history_fh is not None:
            with suppress(OSError):
                session._history_fh.write(chunk)
                session._history_fh.flush()
        subs = list(session.subscribers.values())
    _deliver(subs, {"type": "output", "data": chunk})


def _finalize_session(session: Session) -> None:
    exit_code: int | None = None
    with suppress(Exception):
        exit_code = session.proc.exitstatus
    with session.lock:
        session.alive = False
        session.exit_code = exit_code
        subs = list(session.subscribers.values())
        session.subscribers.clear()
        if session._history_fh is not None:
            with suppress(OSError):
                session._history_fh.close()
            session._history_fh = None
    _deliver(subs, {"type": "exited", "exit_code": exit_code})
    with _sessions_lock:
        _sessions.pop(session.chat_id, None)
    with suppress(Exception):
        agent_registry.touch_chat(session.agent_slug, session.chat_id)
    log.info("chat %s exited (code=%r)", session.chat_id, exit_code)


def _reader_loop(session: Session) -> None:
    while True:
        try:
            chunk = session.proc.read(4096)
        except EOFError:
            break
        except Exception:  # noqa: BLE001 - a broken pty is EOF, not a crash
            log.exception("pty read failed for chat %s", session.chat_id)
            break
        if not chunk:
            break
        _publish_output(session, chunk)
    _finalize_session(session)


# -------------------------------------------------------------------- spawn


def _spawn_session(agent: agent_registry.Agent, chat_id: str) -> Session:
    """Caller must hold `_sessions_lock` and must already have confirmed no
    live session for ``chat_id`` exists. Raises `SessionLimitExceeded`,
    `ClaudeNotFound`, or whatever `PtyProcess.spawn` itself raises (most
    likely `FileNotFoundError` — surfaced as-is, not wrapped, since it is
    already a clear message)."""
    live = sum(1 for s in _sessions.values() if s.alive)
    if live >= config.MAX_LIVE_SESSIONS:
        raise SessionLimitExceeded(
            f"machine-wide live-session cap reached ({config.MAX_LIVE_SESSIONS})"
        )

    launch = agent_registry.read_launch_config(agent.root)
    argv = _build_argv(launch)
    env = _build_env(launch)

    history_path = agent_registry.chat_history_path(agent.root, chat_id)
    history_path.parent.mkdir(parents=True, exist_ok=True)

    proc = _PtyHandle(argv, cwd=str(agent.root), env=env, dimensions=(24, 80))
    try:
        session = Session(
            chat_id=chat_id, agent_slug=agent.slug, history_path=history_path, proc=proc
        )
        session._history_fh = history_path.open("a", encoding="utf-8")
        _sessions[chat_id] = session

        thread = threading.Thread(
            target=_reader_loop, args=(session,),
            name=f"mnemo-pty-{chat_id[:8]}", daemon=True,
        )
        session.reader_thread = thread
        thread.start()
    except Exception:
        # The process already exists at this point (`_PtyHandle.__init__`
        # above succeeded) but nothing failed BEFORE it that would have
        # prevented the spawn. Anything going wrong from here on — most
        # plausibly `history_path.open()` (disk full, permissions) — must
        # not leave a live, paid `claude` process that `_sessions` never
        # learned about: `stop_session`/`stop_all` can only terminate what
        # they can find, and this session was never added to the table.
        with suppress(Exception):
            proc.terminate(force=True)
        raise

    log.info("spawned chat %s for agent %s (mode=%s)",
              chat_id, agent.slug, launch.get("mode"))
    with suppress(Exception):
        agent_registry.touch_chat(agent.slug, chat_id)
    return session


def _subscribe(session: Session) -> tuple[str, "asyncio.Queue[dict[str, Any]]", str]:
    """Register a new subscriber and read replay text, atomically under
    `session.lock` — see the module docstring's "race-free reconnect" note.
    Returns ``(sub_id, queue, replay_text)``."""
    queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
    sub_id = uuid.uuid4().hex
    with session.lock:
        replay_text = ""
        with suppress(OSError):
            replay_text = session.history_path.read_text(
                encoding="utf-8", errors="replace"
            )
        session.subscribers[sub_id] = queue
    return sub_id, queue, replay_text


def ensure_and_subscribe(
    slug: str, chat_id: str
) -> tuple[str, "asyncio.Queue[dict[str, Any]]", str]:
    """The one entry point the WS route needs: make sure a live session for
    ``(slug, chat_id)`` exists (spawning it on first use), then subscribe to
    it. Returns ``(sub_id, queue, replay_text)``.

    Raises `agent_registry.AgentNotFound`, `agent_registry.ChatNotFound`
    (the chat record must already exist — created via `POST
    /api/agents/{slug}/chats`), `SessionLimitExceeded`, or `ClaudeNotFound`.
    """
    agent = agent_registry.get(slug)
    agent_registry.get_chat(slug, chat_id)  # 404s before anything is spawned

    with _sessions_lock:
        session = _sessions.get(chat_id)
        if session is None or not session.alive:
            session = _spawn_session(agent, chat_id)

    return _subscribe(session)


def unsubscribe(chat_id: str, sub_id: str) -> None:
    with _sessions_lock:
        session = _sessions.get(chat_id)
    if session is None:
        return
    with session.lock:
        session.subscribers.pop(sub_id, None)


def send_input(chat_id: str, data: str) -> bool:
    with _sessions_lock:
        session = _sessions.get(chat_id)
    if session is None or not session.alive:
        return False
    with suppress(Exception):
        session.proc.write(data)
        return True
    return False


def resize(chat_id: str, rows: int, cols: int) -> bool:
    with _sessions_lock:
        session = _sessions.get(chat_id)
    if session is None or not session.alive:
        return False
    with suppress(Exception):
        session.proc.setwinsize(rows, cols)
        return True
    return False


def stop_session(chat_id: str, timeout: float = 10.0) -> bool:
    """Terminate ONE live session's process, if there is one, and wait for
    its reader thread to notice and finalize (which is what actually removes
    the entry from `_sessions`). Used by the chat-delete endpoint. Returns
    whether a live session was found.

    NOT used by `stop_all` below — terminate-then-join-with-the-full-timeout
    one chat_id at a time would bound total shutdown time by N × `timeout`
    for N live sessions rather than one shared `timeout`; see `stop_all`.
    """
    with _sessions_lock:
        session = _sessions.get(chat_id)
    if session is None:
        return False
    with suppress(Exception):
        if session.proc.isalive():
            session.proc.terminate(force=True)
    thread = session.reader_thread
    if thread is not None:
        thread.join(timeout=timeout)
    return True


def stop_all(timeout: float = 10.0) -> None:
    """Called once from `api.py`'s `_shutdown()`. Mirrors `workqueue.stop()`'s
    actual two-phase shape — signal every live session to terminate FIRST,
    THEN join each reader thread — rather than `stop_session`'s
    terminate-then-join-in-full sequence run once per chat_id, which would
    serialize N processes' termination behind N separate `timeout` waits
    instead of the one shared `timeout` this function's contract (and
    `_shutdown`'s comment) promises. The two phases mean every process gets
    signalled to exit before this function starts waiting on any of them, so
    they all shut down concurrently and the total wait is bounded by roughly
    one `timeout`, not N of them.
    """
    with _sessions_lock:
        sessions = list(_sessions.values())

    for session in sessions:
        with suppress(Exception):
            if session.proc.isalive():
                session.proc.terminate(force=True)

    for session in sessions:
        thread = session.reader_thread
        if thread is not None:
            thread.join(timeout=timeout)
