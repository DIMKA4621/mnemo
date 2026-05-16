"""MCP face of the engine — the SAME code, exposed as agent-callable tools.

Not a daemon: the client spawns `mem-index mcp` over stdio for the
session and kills it on exit. The project root comes from cwd (or
$MEMORY_POC_ROOT), so a project-level .mcp.json scopes it to that
project's memory automatically.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .index import reindex
from .search import search

server = FastMCP("memory-poc")


@server.tool()
def memory_search(
    query: str,
    scope: str | None = None,
    agent: str | None = None,
    top_k: int = 5,
) -> str:
    """Search this project's memory. `scope`: 'project' or 'agent';
    `agent`: restrict to one agent's memory."""
    hits = search(query, scope=scope, agent_name=agent, top_k=top_k)
    if not hits:
        return "No relevant results."
    out = []
    for i, h in enumerate(hits, 1):
        tag = h.scope + (f"/{h.agent_name}" if h.agent_name else "")
        snippet = " ".join(h.content.split())[:400]
        out.append(
            f"[{i}] {h.path} · {h.heading or '(no heading)'} · {tag} "
            f"· score={h.score:.4f}\n{snippet}"
        )
    return "\n\n".join(out)


@server.tool()
def memory_reindex() -> str:
    """Reconcile this project's .md memory into the index (hash-diff +
    prune). Call after writing/editing memory files."""
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        reindex(verbose=True)
    return buf.getvalue().strip() or "reconcile done (no changes)."


def run() -> None:
    server.run()  # stdio transport by default
