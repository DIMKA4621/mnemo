"""MCP over HTTP, mounted into the backend's own uvicorn.

v2 ran an MCP server per session over stdio, which meant Claude Code spawned
a process (and on Windows, flashed a console) for every project. v3 mounts
one streamable-HTTP MCP app into the running service, so a session *connects*
instead of spawning: NFR-2, and the reason `mnemo mcp` no longer exists.

**The token is the whole address** (team-lead decision):

    http://127.0.0.1:8918/mcp?token=<bank-token>

Once a token belongs to a bank, naming the bank again in the URL is redundant
— and worse than redundant, because two things that say which bank can
disagree. So the plain face has **no path segment**: the presented token is
resolved to a bank by `registry.resolve_by_token`, in the auth middleware,
before the request gets here. `Authorization: Bearer <bank-token>` works
identically, for a client that prefers headers.

Two whole classes of bug went away with the segment, and neither is worth
recreating: the `raw_path` / `root_path` trap that a mounted app's path
rewriting sets (it once cost a 404 that read as a broken handshake), and
percent-encoding a bank name that routinely holds spaces and Cyrillic.

What the URL no longer says is *which* bank an entry is for. That meaning
moved to the **MCP server entry name** (`mnemo`, `mnemo-notes`) — which is
what a person actually reads in a config anyway. A cosmetic segment that
routing ignored would be worse than none: a path component that does not mean
what it says is read as routing by the next person.

The tools call the backend's internal functions **directly**. Self-HTTP from
a mounted app would mean a second socket, a duplicate journal entry per call,
and a request that can deadlock behind the very worker it is waiting on.

**This face is read-only: `search` and `tree`, and nothing else.** Registering
banks, dropping them, forcing a reindex and reading the journal live on the
admin face (`src/mcp_admin.py`, mounted at `/mcp-admin`), which opens only for
the service token. A project's own wiring holds that project's bank token, and
that token buys exactly two read tools on exactly one bank.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any

from mcp.server.mcpserver import MCPServer

log = logging.getLogger("mnemo.mcp")

# The bank this request was authenticated as, lifted out of the ASGI scope by
# the shim below. A ContextVar because a tool body has no access to the HTTP
# request, and this is per-request state on an async stack.
#
# It holds a bank **id**, not a name or a user-supplied string: it is set from
# a `Bank` the middleware already resolved, so nothing here re-parses anything
# a caller sent.
current_bank_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mnemo_current_bank_id", default=None
)

_mcp: MCPServer | None = None


# ------------------------------------------------------------------ tools


def _resolve(explicit: str | None):
    """Resolve this call's bank, or return text the agent can act on.

    Returns ``(bank, None)`` or ``(None, message)``. A tool must not raise at
    an agent: a message telling it what to do next is actionable, an exception
    just ends the turn.

    Two callers, two paths, and no fallback chain between them:

    * **the plain face** passes ``explicit=None`` and gets the bank its
      *token* authenticated as. On this path a miss is impossible — auth
      already 401'd anything that resolved to nothing — so there is no guess
      left to make.
    * **`/mcp-tools/*` and the admin tools** pass a bank by id, name or path.
      Both sit behind the service token, which already reaches every bank, so
      naming one adds no reach; it is simply how those surfaces work.

    The old four-level chain (argument, URL segment, header, "the only bank")
    is gone. Every level after the first existed to recover a bank the caller
    had not named — and with the token as the address there is nothing to
    recover, only opportunities to disagree with it.
    """
    from . import registry
    from .registry import AmbiguousBankRef, BankNotFound

    if explicit and str(explicit).strip():
        try:
            return registry.resolve(str(explicit).strip()), None
        except (BankNotFound, AmbiguousBankRef) as exc:
            names = ", ".join(b.name for b in registry.load()) or "(none registered)"
            return None, (
                f"[mnemo] {exc}\n"
                f"Registered banks: {names}"
            )

    bank_id = current_bank_id.get()
    if bank_id:
        try:
            return registry.get(bank_id), None
        except BankNotFound:
            # The bank was deregistered between authentication and this call.
            return None, ("[mnemo] this connection's bank has been removed "
                          "from the registry.")
    return None, (
        "[mnemo] this request carried no bank. The MCP face is addressed by a "
        "bank token — check the `token=` in this server's URL."
    )


def _status_line(bank, payload: dict) -> str:
    """Status first, so an agent sees the bank's state without a second call."""
    return (
        f"[mnemo · bank={bank.name} · status={payload['status']} "
        f"· queued={payload['queued']} · chunks={payload['chunk_count']}]"
    )


def _no_hits_text(payload: dict) -> str:
    """Three states an agent must act on differently (§10.2, decision #11)."""
    if payload["status"] == "indexing" and payload["chunk_count"] == 0:
        return "Bank is still building its first index — retry shortly."
    if payload["status"] == "empty":
        return "Bank has nothing indexed yet."
    return "No relevant results."


# ------------------------------------------------ tool bodies (shared)
#
# The bodies live OUTSIDE `_register` so the `/mcp-tools/*` mirror (§9.8) can
# call the very same code. That is the whole point of the mirror: identical
# names, identical parameters, identical text. Had it re-implemented the
# formatting, the two would have drifted the first time either changed — and a
# debug surface that quietly disagrees with what the agent reads is worse than
# no debug surface at all.


def _domain_problem(exc: Exception) -> str:
    """An `ApiError` rendered as text the agent can act on.

    Reachable now in a way it was not before: a bank token authenticates even
    when its bank is **disabled**, which is deliberate — the credential is
    genuine, and "this bank is switched off" is a far more useful answer than
    a 401 the holder cannot tell from a wrong token. But `api_search` raises
    `bank_not_found` for a disabled bank, and an exception out of a tool ends
    the agent's turn. So it is caught and spoken.

    The same path carries `bank_stale`, which an agent can act on in the one
    way that matters: it says the answer is missing because the index was
    built by a different embedding model, not because the memory is empty.
    Those two are indistinguishable from the outside, and confusing them is
    how an agent concludes nothing was ever recorded.
    """
    from .api import ApiError

    if isinstance(exc, ApiError):
        return f"[mnemo] {exc.code}: {exc.message}"
    raise exc


def run_search(
    query: str,
    top_k: int = 5,
    path_prefix: str | None = None,
    bank: str | None = None,
    *,
    face: str = "mcp",
) -> str:
    """Search this project's curated memory. Returns numbered sections."""
    from .api import SearchRequest, api_search

    target, problem = _resolve(bank)
    if problem:
        return problem
    try:
        payload = api_search(SearchRequest(
            bank=target.id, query=query, top_k=max(1, min(int(top_k), 50)),
            path_prefix=path_prefix, face=face,
        ))
    except Exception as exc:  # noqa: BLE001 - a tool answers, it does not raise
        return _domain_problem(exc)
    lines = [_status_line(target, payload)]
    if not payload["hits"]:
        lines.append(_no_hits_text(payload))
        return "\n".join(lines)
    for i, hit in enumerate(payload["hits"], 1):
        lines.append(
            f"[{i}] {hit['path']} · {hit['heading'] or '(no heading)'} "
            f"· score={hit['score']:.4f}"
        )
        lines.append(hit["content"])
    return "\n".join(lines)


def run_tree(
    path_prefix: str | None = None,
    depth: int = 3,
    bank: str | None = None,
) -> str:
    """Show the memory tree, with each file's headings."""
    from .api import api_tree

    target, problem = _resolve(bank)
    if problem:
        return problem
    try:
        payload = api_tree(bank=target.id, links=False, depth=max(0, int(depth)))
    except Exception as exc:  # noqa: BLE001 - a tool answers, it does not raise
        return _domain_problem(exc)
    lines = [f"[mnemo · bank={target.name} · {payload['files']} files]"]

    def walk(node: dict, indent: int) -> None:
        for child in node.get("children", []):
            rel = child["path"]
            if path_prefix and not (
                rel == path_prefix
                or rel.startswith(path_prefix + "/")
                or path_prefix.startswith(rel + "/")
            ):
                continue
            pad = "  " * indent
            if child["type"] == "dir":
                lines.append(f"{pad}{child['name']}/")
                walk(child, indent + 1)
            else:
                heads = ", ".join(child.get("headings") or [])
                lines.append(
                    f"{pad}{child['name']}" + (f"  — {heads}" if heads else "")
                )

    walk(payload["tree"], 0)
    return "\n".join(lines)


def run_reindex(
    path: str | None = None,
    bank: str | None = None,
    full: bool = False,
) -> str:
    """Queue a reindex of one file, or of the whole bank."""
    from .api import ReindexRequest, api_reindex

    target, problem = _resolve(bank)
    if problem:
        return problem
    payload = api_reindex(ReindexRequest(bank=target.id, path=path, full=full))
    return (f"[mnemo · bank={target.name}] queued "
            f"{len(payload['task_ids'])} task(s); "
            f"{payload['queued']} waiting.")


def _register(mcp: MCPServer) -> None:
    """The plain face: two read-only tools, and the bank is not an argument.

    **`bank` is gone from the tool schemas on purpose.** A connection pinned
    to one bank used to be redirectable by passing `bank=` to a tool —
    measured live, `/mcp/mnemo` calling `search(bank="odin-crm")` returned
    odin-crm's memory — which defeats per-connection addressing and, with
    per-bank tokens, would have made the token a key to any bank the holder
    could name.

    **The bank now comes from the presented token and from nothing else**
    (§10.3). The URL segment, the `X-Mnemo-Bank` header and the
    "if there is only one bank it must be that one" rule are all gone, and
    each for the same reason: with the token as the address, every one of them
    is a second thing saying which bank, free to disagree with the credential.
    The failure mode that buys is the worst available — a request that
    succeeds against the wrong bank and looks entirely normal.

    The shared `run_*` bodies below KEEP their `bank` parameter, and the
    asymmetry is deliberate: `/mcp-tools/*` (§9.8) is a hand-testing surface
    behind the **service** token, where naming a bank per call is the whole
    point.

    **`reindex` is not here** — it moved to the admin face (`src/mcp_admin.py`).
    The watcher reindexes on its own within seconds of a save, so on a project
    face `reindex` is a tool slot spent in every single session on a button
    almost nobody needs to press.

    Declarations only. The docstring is what the agent reads as the tool's
    description, so it stays on the decorated function — that is where
    `@mcp.tool()` looks for it.
    """

    @mcp.tool()
    def search(
        query: str,
        top_k: int = 5,
        path_prefix: str | None = None,
    ) -> str:
        """Search this project's curated memory. Returns numbered sections."""
        return run_search(query, top_k, path_prefix)

    @mcp.tool()
    def tree(path_prefix: str | None = None, depth: int = 3) -> str:
        """Show the memory tree, with each file's headings."""
        return run_tree(path_prefix, depth)


def server() -> MCPServer:
    """The single MCPServer instance, built once."""
    global _mcp
    if _mcp is None:
        _mcp = MCPServer("mnemo")
        _register(_mcp)
    return _mcp


# ------------------------------------------------------------------- ASGI


class AuthenticatedBankASGI:
    """Lifts the authenticated bank out of the ASGI scope into the ContextVar.

    All that is left of what used to be `BankRoutingASGI`. That class existed
    to cut ``/<bank-name>`` off the path and stash it — and carried a comment
    block about a `raw_path` / `root_path` trap that once cost a 404 looking
    like a broken handshake, plus the double-unquoting rule for a bank name
    holding `/` or `%20`. **None of it survives the segment's removal**, and
    none of it is worth keeping "just in case": there is no segment to parse,
    so there is no path to rewrite and nothing to decode.

    What remains is a handover. `api.auth_middleware` has already resolved the
    presented token to a bank and written its id into ``scope``; a tool body
    cannot see a scope, so this puts it where the body can read it. The value
    is never parsed here and never comes from the request — only from the
    middleware, which is the only place that has verified anything.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from .api import BANK_SCOPE_KEY  # noqa: PLC0415 - avoids a cycle

        token = current_bank_id.set(scope.get(BANK_SCOPE_KEY))
        try:
            await self.app(scope, receive, send)
        finally:
            current_bank_id.reset(token)


def build_app() -> Any:
    """The ASGI app to mount at ``/mcp``.

    ``stateless_http``: every request is self-contained, which is what lets one
    mounted app serve many banks and many sessions with no session affinity —
    each request carries its own bank in its own token.
    ``streamable_http_path="/"`` because the mount point is the whole path.

    Both settings sit here rather than on the constructor: the 2.0 SDK moved
    them out of ``MCPServer(...)`` and into this call. That also fixes their
    order for good — ``session_manager`` raises until this has run, and
    ``api.lifespan`` reads it, so the mount must be built first. It is:
    ``api`` mounts at import, the lifespan runs at startup.
    """
    return AuthenticatedBankASGI(
        server().streamable_http_app(streamable_http_path="/", stateless_http=True)
    )
