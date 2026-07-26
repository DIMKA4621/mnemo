"""MCP over HTTP, mounted into the backend's own uvicorn.

v2 ran an MCP server per session over stdio, which meant Claude Code spawned
a process (and on Windows, flashed a console) for every project. v3 mounts
one streamable-HTTP MCP app into the running service, so a session *connects*
instead of spawning: NFR-2, and the reason `mnemo mcp` no longer exists.

**The bank and the token both travel in the URL** (team-lead decision):

    http://127.0.0.1:8918/mcp/<bank-name>?token=${MNEMO_API_TOKEN}

Both were headers in the original design. That made the whole face depend on
Claude Code forwarding `headers` from `.mcp.json` for `type: http` — and if
it does not, `Authorization` does not arrive either, so **auth** fails, not
merely bank addressing. The URL form has no such dependency. The bank is a
**name**, which survives a clone; the token comes from the environment, so
nothing machine-specific and no secret enters git. Headers remain supported
as an override, never as a requirement.

The tools call the backend's internal functions **directly**. Self-HTTP from
a mounted app would mean a second socket, a duplicate journal entry per call,
and a request that can deadlock behind the very worker it is waiting on.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

log = logging.getLogger("mnemo.mcp")

# The bank this request is addressed to, taken from the URL path segment by
# the ASGI shim below. A ContextVar because a tool body has no access to the
# HTTP request, and this is per-request state on an async stack.
current_bank: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mnemo_current_bank", default=None
)
# Header override, for a client that does send one.
current_header_bank: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mnemo_current_header_bank", default=None
)

_mcp: FastMCP | None = None


# ------------------------------------------------------------------ tools


def _resolve(explicit: str | None):
    """Resolve this call's bank, or return text the agent can act on.

    Returns ``(bank, None)`` or ``(None, message)``. A tool must not raise at
    an agent: a message listing the available banks tells it what to do next,
    an exception just ends the turn.
    """
    from . import registry
    from .api import resolve_bank_ref
    from .registry import AmbiguousBankRef, BankNotFound

    try:
        return resolve_bank_ref(
            explicit=explicit,
            header=current_header_bank.get(),
            url_segment=current_bank.get(),
        ), None
    except (BankNotFound, AmbiguousBankRef) as exc:
        names = ", ".join(b.name for b in registry.load()) or "(none registered)"
        return None, (
            f"[mnemo] {exc}\n"
            f"Available banks: {names}\n"
            f"Pass one as the `bank` argument, or register a root with "
            f"`mnemo banks add <path>`."
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


def _register(mcp: FastMCP) -> None:
    @mcp.tool()
    def memory_search(
        query: str,
        top_k: int = 5,
        path_prefix: str | None = None,
        bank: str | None = None,
    ) -> str:
        """Search this project's curated memory. Returns numbered sections."""
        from .api import SearchRequest, api_search

        target, problem = _resolve(bank)
        if problem:
            return problem
        payload = api_search(SearchRequest(
            bank=target.id, query=query, top_k=max(1, min(int(top_k), 50)),
            path_prefix=path_prefix, face="mcp",
        ))
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

    @mcp.tool()
    def memory_tree(
        path_prefix: str | None = None,
        depth: int = 3,
        bank: str | None = None,
    ) -> str:
        """Show the memory tree, with each file's headings."""
        from .api import api_tree

        target, problem = _resolve(bank)
        if problem:
            return problem
        payload = api_tree(bank=target.id, links=False, depth=max(0, int(depth)))
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

    @mcp.tool()
    def memory_reindex(path: str | None = None, bank: str | None = None) -> str:
        """Queue a reindex of one file, or of the whole bank."""
        from .api import ReindexRequest, api_reindex

        target, problem = _resolve(bank)
        if problem:
            return problem
        payload = api_reindex(ReindexRequest(bank=target.id, path=path,
                                             full=False))
        return (f"[mnemo · bank={target.name}] queued "
                f"{len(payload['task_ids'])} task(s); "
                f"{payload['queued']} waiting.")


def server() -> FastMCP:
    """The single FastMCP instance, built once."""
    global _mcp
    if _mcp is None:
        # stateless_http: every request is self-contained, which is what lets
        # one mounted app serve many banks and many sessions with no session
        # affinity. streamable_http_path="/" because the bank segment is
        # stripped off before the inner app ever sees the request.
        _mcp = FastMCP("mnemo", stateless_http=True, streamable_http_path="/")
        _register(_mcp)
    return _mcp


# ------------------------------------------------------------------- ASGI


class BankRoutingASGI:
    """Strips ``/<bank-name>`` off the path and remembers it for the tools.

    FastAPI cannot mount a sub-app under a path *parameter*, and the MCP app
    owns everything below its mount point. So the routing lives here: one
    small shim, rather than one mounted app per bank — which would have to be
    rebuilt every time a bank is registered.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        # Starlette keeps the FULL path in `scope["path"]` and strips
        # `root_path` only when matching (`starlette.routing.get_route_path`).
        # So the bank segment has to be cut from the part *after* root_path,
        # and the rewritten path must keep root_path on the front — otherwise
        # the inner router subtracts the prefix from a path that no longer
        # has it and matches nothing. This cost a 404 that looked like a
        # broken handshake.
        path = scope.get("path", "/") or "/"
        root = scope.get("root_path", "") or ""
        route_path = path[len(root):] if path.startswith(root) else path

        segment: str | None = None
        rest = "/"
        trimmed = route_path.lstrip("/")
        if trimmed:
            head, _, tail = trimmed.partition("/")
            segment = head or None
            rest = "/" + tail if tail else "/"

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        new_path = root + rest
        scope = dict(scope, path=new_path, raw_path=new_path.encode())

        tok_bank = current_bank.set(segment)
        tok_hdr = current_header_bank.set(headers.get("x-mnemo-bank"))
        try:
            await self.app(scope, receive, send)
        finally:
            current_bank.reset(tok_bank)
            current_header_bank.reset(tok_hdr)


def build_app() -> Any:
    """The ASGI app to mount at ``/mcp``."""
    return BankRoutingASGI(server().streamable_http_app())
