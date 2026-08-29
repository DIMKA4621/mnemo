"""The agent-to-agent MCP face (MN-45a) — mounted at ``/mcp-agents``, same
pattern as ``mcp_server.py``/``mcp_admin.py``: a real ``mnemo`` MCP server,
mounted into the running service, reached only through ``api.py``'s
``auth_middleware``.

**A third kind of credential.** Neither a bank token nor the service token
opens this face — a per-session token, minted by ``agent_runtime.py`` the
moment a live chat is spawned and gone the moment it ends
(``agent_runtime.resolve_session_token``). The caller's own identity (its
``chat_id``, and through that its agent slug) is therefore always known
server-side, from the credential alone — never from anything a tool argument
claims. That is what makes ``send_message``'s ``[from <agent> · <chat>] ``
prefix trustworthy: nothing here ever takes "who is calling" as user input.

**Why this exists as a side channel and not a PTY read.** MN-43 deliberately
never parses the raw byte stream a live ``claude`` process produces — see
its module docstring. Claude Code CLI's own ``--mcp-config``/``--settings``
flags (confirmed live, ``.claude/scratch/mn45-verify/gate_spike.py``,
2026-08-29) give a session a documented, sanctioned way to reach an
additional tool surface without mnemo ever touching that stream.

**Machine-wide on purpose.** Every tool here reaches every live session on
this machine, not just the caller's own agent — the same scope
``SendMessage``/``ListAgents`` have in *this* harness, and the whole point of
the coordinator/worker pattern the ticket names (MN-45 Jira decision #1).
The trade — one user, own tooling, loopback-only, no isolation between
principals — is the trust model the rest of mnemo already runs on, not a new
exception. The one thing that changes to make that acceptable at this reach
is that **every call is audited**: `_audit` below logs every tool invocation
to `servicelog.log_agent_call` before anything else happens in the tool body,
per the ticket's explicit condition (MN-45 Jira decision #5) — reach is not
limited, but it is never a silent channel.
"""
from __future__ import annotations

import contextvars
import logging
import re
import time
from typing import Any

from mcp.server.mcpserver import MCPServer

log = logging.getLogger("mnemo.mcp.agents")

# The chat_id this request was authenticated as, lifted out of the ASGI scope
# by the shim below — mirrors `mcp_server.current_bank_id` exactly, one
# ContextVar per per-request identity a face needs and a tool body cannot
# otherwise reach.
current_chat_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mnemo_current_agent_chat_id", default=None
)

_mcp: MCPServer | None = None

_MAX_TAIL_CHARS = 100_000

# Strips PTY ANSI escape sequences (CSI, and simple ESC-prefixed C1 codes)
# for `read_recent`/`session_summary`. Best-effort readability, not a
# terminal emulator — matches MN-44's own stated bar for anything reading
# `history.log` as text.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI sequences (colors, cursor moves, ...)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences (titles, ...)
    r"|\x1b[@-Z\\-_]"  # simple two-byte escapes
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _tail(text: str, n_chars: int) -> str:
    n = max(1, min(int(n_chars), _MAX_TAIL_CHARS))
    return text[-n:]


# ------------------------------------------------------------------ audit


def _audit(tool: str, target: str | None = None) -> None:
    """Log this call BEFORE the tool body does anything else — see the
    module docstring's "every call is audited" note. Telemetry never breaks
    the tool it observes (`servicelog.log_agent_call` swallows its own
    errors), so this is never a reason a tool call fails."""
    from . import agent_runtime, servicelog

    caller_chat_id = current_chat_id.get() or ""
    caller_slug = agent_runtime.session_agent_slug(caller_chat_id) or ""
    servicelog.log_agent_call(
        caller_chat_id=caller_chat_id, caller_slug=caller_slug,
        tool=tool, target=target,
    )


def _caller() -> tuple[str, str] | None:
    """``(chat_id, agent_slug)`` for the current call, or `None` if this
    connection somehow carries no session — unreachable in practice once
    auth has resolved a token, kept as an explicit answer rather than a
    crash if it ever is."""
    from . import agent_runtime

    chat_id = current_chat_id.get()
    if not chat_id:
        return None
    return chat_id, (agent_runtime.session_agent_slug(chat_id) or "?")


# ------------------------------------------------------------ tool bodies
#
# Module-level, like `mcp_server.py`'s `run_*` functions — kept separate from
# `_register` so they can be unit-tested directly, without a real MCP client.


def run_list_sessions() -> str:
    """Every currently-live agent chat session on this machine."""
    from . import agent_runtime

    _audit("list_sessions")
    sessions = agent_runtime.list_live_sessions()
    if not sessions:
        return "[mnemo-agents] no live sessions right now."
    now = time.time()
    lines = [f"[mnemo-agents · {len(sessions)} live session(s)]"]
    for s in sorted(sessions, key=lambda s: s["created_at"]):
        age = max(0.0, now - s["created_at"])
        lines.append(
            f"{s['agent_slug']}  chat={s['chat_id']}  "
            f"alive={s['alive']}  up {age:.0f}s"
        )
    return "\n".join(lines)


def _resolve_target(to: str) -> tuple[str | None, str]:
    """``(chat_id, note)`` for `send_message`'s ``to`` — a live chat_id
    matched as-is, or an agent slug resolved to its most-recently-active
    LIVE session. ``chat_id`` is `None` when nothing live matches; ``note``
    explains why, for the caller to read."""
    from . import agent_registry, agent_runtime

    live = agent_runtime.list_live_sessions()
    live_ids = {s["chat_id"] for s in live}
    if to in live_ids:
        return to, ""

    matching = [s for s in live if s["agent_slug"] == to]
    if not matching:
        return None, f"no live session for chat_id or agent slug {to!r}."
    if len(matching) == 1:
        return matching[0]["chat_id"], ""

    # More than one live session for this slug — break the tie the same way
    # a human picking a chat tab would: most-recently-active first
    # (`list_chats` is already sorted that way).
    try:
        ordered = agent_registry.list_chats(to)
    except agent_registry.AgentNotFound:
        ordered = []
    live_set = {m["chat_id"] for m in matching}
    for chat in ordered:
        if chat["chat_id"] in live_set:
            return chat["chat_id"], ""
    return matching[0]["chat_id"], ""


def run_send_message(to: str, message: str) -> str:
    """Send a message into another live session's terminal input, addressed
    by chat_id or by agent slug (its most-recently-active live session)."""
    from . import agent_runtime

    caller = _caller()
    _audit("send_message", target=to)
    if caller is None:
        return "[mnemo-agents] this connection carried no session."
    caller_chat_id, caller_slug = caller

    target_chat_id, note = _resolve_target(to)
    if target_chat_id is None:
        return f"[mnemo-agents] {note}"

    # The server supplies this prefix — never the caller — which is what
    # makes it trustworthy for the human or agent reading the target
    # session's terminal: nobody can claim to be a different sender.
    prefixed = f"[from {caller_slug} · {caller_chat_id[:8]}] {message}"
    delivered = agent_runtime.send_input(target_chat_id, prefixed + "\r")
    if not delivered:
        return (f"[mnemo-agents] session {target_chat_id} is not live; "
                "nothing delivered.")
    return f"[mnemo-agents] delivered to {target_chat_id}."


def _find_chat(chat_id: str):
    """``(Agent, chat_info)`` for a chat_id that could belong to ANY agent on
    this machine — the coordination tools are addressed by chat_id alone, so
    there is no slug to narrow the search with. A linear scan over every
    registered agent's chats is the v1 answer (`list_agents()` is small by
    construction — this is not the tuned real policy, MN-46 owns that for
    the runtime itself); returns `None` if no agent has this chat."""
    from . import agent_registry

    for agent in agent_registry.list_agents():
        try:
            chat = agent_registry.get_chat(agent.slug, chat_id)
        except agent_registry.ChatNotFound:
            continue
        return agent, chat
    return None


def run_read_recent(chat_id: str, n_chars: int = 4000) -> str:
    """Tail of a chat's raw output, ANSI escape codes stripped."""
    from . import agent_registry

    _audit("read_recent", target=chat_id)
    found = _find_chat(chat_id)
    if found is None:
        return f"[mnemo-agents] no chat {chat_id!r} found on this machine."
    agent, _chat = found
    history_path = agent_registry.chat_history_path(agent.root, chat_id)
    try:
        raw = history_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    tail = _tail(_strip_ansi(raw), n_chars)
    header = f"[mnemo-agents · {agent.slug} · chat={chat_id}]"
    return f"{header}\n{tail}" if tail else f"{header} (nothing recorded yet)"


def run_session_summary(chat_id: str, n_chars: int = 4000) -> str:
    """Session metadata plus a recent-output tail — deliberately NOT an
    LLM-generated summary. mnemo's backend has no completion capability
    (only embeddings for `search`), so there is nothing here that could read
    the transcript and condense it; this hands back the raw facts and the
    raw recent text instead of pretending otherwise."""
    from . import agent_registry, agent_runtime

    _audit("session_summary", target=chat_id)
    found = _find_chat(chat_id)
    if found is None:
        return f"[mnemo-agents] no chat {chat_id!r} found on this machine."
    agent, chat = found

    live_by_id = {s["chat_id"]: s for s in agent_runtime.list_live_sessions()}
    alive = chat_id in live_by_id and live_by_id[chat_id]["alive"]

    # MN-45b: real counts from the `subagents.jsonl` sidecar, not a
    # placeholder. "started, no completion signal yet" rather than
    # "running" — a crashed subagent never sends a Stop event, so this
    # can go stale; see `agent_runtime.record_subagent_event`'s docstring.
    events = agent_registry.read_subagent_events(agent.root, chat_id)
    started = [e for e in events if e.get("hook_event_name") == "SubagentStart"]
    stopped_ids = {
        e.get("agent_id") for e in events if e.get("hook_event_name") == "SubagentStop"
    }
    pending = sum(1 for e in started if e.get("agent_id") not in stopped_ids)
    subagent_line = f"subagent runs: {len(started)}"
    if pending:
        subagent_line += f" ({pending} started, no completion signal yet)"

    lines = [
        f"[mnemo-agents · {agent.slug} · chat={chat_id}]",
        f"created_at={chat.get('created_at') or '?'}  "
        f"last_active_at={chat.get('last_active_at') or '?'}  alive={alive}",
        subagent_line,
    ]
    history_path = agent_registry.chat_history_path(agent.root, chat_id)
    try:
        raw = history_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    tail = _tail(_strip_ansi(raw), n_chars)
    if tail:
        lines.append("--- recent output ---")
        lines.append(tail)
    return "\n".join(lines)


def run_interrupt(chat_id: str) -> str:
    """Send a soft interrupt (Escape) to a live session — does not kill the
    process. Deleting the chat already does that through a separate, existing
    path; v1 ships only the soft form (MN-45 Jira decision #4)."""
    from . import agent_runtime

    _audit("interrupt", target=chat_id)
    delivered = agent_runtime.send_input(chat_id, "\x1b")
    if not delivered:
        return f"[mnemo-agents] no live session {chat_id!r} to interrupt."
    return f"[mnemo-agents] sent a soft interrupt (Escape) to {chat_id}."


# ---------------------------------------------------------------- register


def _register(mcp: MCPServer) -> None:
    """The five coordination tools. Declarations only — the bodies are
    above, and each is audited before it does anything else (`_audit`)."""

    @mcp.tool()
    def list_sessions() -> str:
        """Every currently-live agent chat session on this machine — agent
        slug, chat_id, alive status, roughly how long it has been running."""
        return run_list_sessions()

    @mcp.tool()
    def send_message(to: str, message: str) -> str:
        """Send a message into another live session's terminal input. `to`
        is a chat_id, or an agent slug (its most-recently-active live
        session). The message is delivered prefixed with the sender's own
        identity, supplied by the server — this cannot be spoofed."""
        return run_send_message(to, message)

    @mcp.tool()
    def read_recent(chat_id: str, n_chars: int = 4000) -> str:
        """Tail of one chat's raw terminal output, ANSI codes stripped."""
        return run_read_recent(chat_id, n_chars)

    @mcp.tool()
    def session_summary(chat_id: str, n_chars: int = 4000) -> str:
        """Session metadata (created/last-active/alive) plus a recent-output
        tail. Not an LLM-written summary — mnemo has no completion
        capability to produce one."""
        return run_session_summary(chat_id, n_chars)

    @mcp.tool()
    def interrupt(chat_id: str) -> str:
        """Send a soft interrupt (Escape) to a live session. Does not kill
        the process."""
        return run_interrupt(chat_id)


def server() -> MCPServer:
    """The single MCPServer instance, built once. A distinct server name
    (`mnemo-agents`) so tools namespace as `mcp__mnemo-agents__send_message`,
    never colliding with the plain (`mnemo`) or admin (`mnemo-admin`) faces."""
    global _mcp
    if _mcp is None:
        _mcp = MCPServer("mnemo-agents")
        _register(_mcp)
    return _mcp


# ------------------------------------------------------------------- ASGI


class AuthenticatedAgentSessionASGI:
    """Lifts the authenticated session's chat_id out of the ASGI scope into
    `current_chat_id` — mirrors `mcp_server.AuthenticatedBankASGI` exactly.
    `api.auth_middleware` has already resolved the presented token via
    `agent_runtime.resolve_session_token` and written the chat_id into
    ``scope``; a tool body cannot see a scope, so this hands it over."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from .api import AGENT_SESSION_SCOPE_KEY  # noqa: PLC0415 - avoids a cycle

        token = current_chat_id.set(scope.get(AGENT_SESSION_SCOPE_KEY))
        try:
            await self.app(scope, receive, send)
        finally:
            current_chat_id.reset(token)


def build_app() -> Any:
    """The ASGI app to mount at ``/mcp-agents``. Same ``stateless_http`` /
    ``streamable_http_path`` shape as the other two faces — see
    `mcp_server.build_app` for why both live on this call rather than the
    constructor."""
    return AuthenticatedAgentSessionASGI(
        server().streamable_http_app(streamable_http_path="/", stateless_http=True)
    )
