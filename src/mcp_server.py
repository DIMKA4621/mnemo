"""MCP face of the engine — the SAME code, exposed as agent-callable tools.

Not a daemon: the client spawns `mnemo mcp` over stdio for the session and
kills it on exit. The bank root comes from cwd (or $MNEMO_ROOT), so a
project-level .mcp.json scopes it to that bank automatically. Phase 4
replaces this with an HTTP endpoint mounted in the backend, so nothing is
spawned at all.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .cli import _search_bank
from .index import reindex

server = FastMCP("mnemo")


@server.tool()
def memory_search(
    query: str,
    path_prefix: str | None = None,
    top_k: int = 5,
) -> str:
    """Search this bank. `path_prefix`: narrow to a folder inside it."""
    # Embed via the warm resident so this long-lived MCP process never
    # loads the ~2.2 GB model itself. qvec=None lets search() fall back
    # to an in-process embed if the resident is unreachable.
    # Wall-clock bounded so a stuck resident never wedges an agent turn.
    import time

    from .config import INJECT_BUDGET_S
    from .embed_server import embed_query_via_server

    t0 = time.monotonic()
    qvec = embed_query_via_server(query, budget_s=INJECT_BUDGET_S)
    if (time.monotonic() - t0) >= INJECT_BUDGET_S:
        return "Search timed out."
    hits = _search_bank(None, query, path_prefix=path_prefix, top_k=top_k,
                        qvec=qvec)
    if not hits:
        return "No relevant results."
    out = []
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h.content.split())[:400]
        out.append(
            f"[{i}] {h.path} · {h.heading or '(no heading)'} "
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
