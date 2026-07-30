"""Standalone MCP test client — proves the agent-callable layer.

Talks to the **running service** the way Claude Code does: streamable HTTP at
`/mcp/<bank>?token=…`, no process spawned (NFR-2). Lists tools, calls
`search` and `tree`, and then checks the one property that keeps
the `/mcp-tools/*` mirror honest — that it returns the *same bytes* as the tool
it mirrors (§9.8).

The previous version spawned `mnemo mcp` over stdio. That subcommand died with
phase 4, so the test had been failing for reasons that had nothing to do with
the code under test.

    .venv/bin/python tests/test_mcp.py

Requires a live backend and at least one indexed bank; exits **2** (not 1) if
there is none, so "cannot run" never reads as "failed".
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

BASE = os.environ.get("MNEMO_TEST_BASE_URL", "http://127.0.0.1:8918")
_token_file = Path.home() / ".claude" / "mnemo" / "state" / "api.token"

_passed = _failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def _text(result) -> str:
    return "\n".join(
        c.text for c in result.content if getattr(c, "type", "") == "text"
    )


def _token() -> str | None:
    token = os.environ.get("MNEMO_API_TOKEN")
    if token:
        return token.strip()
    try:
        return _token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _get(path: str, token: str) -> str:
    req = urllib.request.Request(
        BASE + path, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def _pick_bank(token: str) -> str | None:
    """A ready bank with chunks in it — the mirror comparison needs hits."""
    try:
        banks = json.loads(_get("/api/banks", token))["banks"]
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return None
    ready = [b for b in banks if b.get("status") == "ready" and b.get("chunks")]
    return ready[0]["name"] if ready else None


async def main() -> int:
    token = _token()
    if not token:
        print(f"SKIP  no API token ({_token_file}); is the engine installed?")
        return 2
    bank = _pick_bank(token)
    if not bank:
        print("SKIP  backend unreachable, or no ready bank with an index")
        return 2
    print(f"bank under test: {bank}\n")

    quoted = urllib.parse.quote(bank, safe="")
    url = f"{BASE}/mcp/{quoted}?token={token}"
    query = "як деплоїмо на прод і робимо rollback"

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            check(
                "tools exposed",
                {"search", "tree", "reindex"} <= tools,
                detail=str(sorted(tools)),
            )
            check("write is NOT exposed (decision #6)",
                  "write" not in tools, detail=str(sorted(tools)))

            search_text = _text(await session.call_tool(
                "search", {"query": query, "top_k": 2}))
            # The status header is what lets an agent tell "nothing indexed"
            # from "no match" without a second call (§10.2).
            check("search answers with a status header",
                  search_text.startswith(f"[mnemo · bank={bank} ·"),
                  detail=search_text[:120])

            tree_text = _text(await session.call_tool(
                "tree", {"depth": 1}))
            check("tree answers with a file count",
                  tree_text.startswith(f"[mnemo · bank={bank} ·")
                  and "files]" in tree_text.splitlines()[0],
                  detail=tree_text[:120])

    # The mirror is only useful if it cannot disagree. Same query, same
    # parameters, compared as bytes — this is the check that fails if either
    # side ever grows its own formatting.
    q = urllib.parse.quote(query)
    b = urllib.parse.quote(bank, safe="")
    mirror_text = _get(
        f"/mcp-tools/search?bank={b}&query={q}&top_k=2", token)
    check("mirror /mcp-tools/search is byte-identical to the tool",
          mirror_text == search_text,
          detail=f"mcp={len(search_text)}b mirror={len(mirror_text)}b")

    mirror_tree = _get(f"/mcp-tools/tree?bank={b}&depth=1", token)
    check("mirror /mcp-tools/tree is byte-identical to the tool",
          mirror_tree == tree_text,
          detail=f"mcp={len(tree_text)}b mirror={len(mirror_tree)}b")

    envelope = json.loads(_get(
        f"/mcp-tools/search?bank={b}&query={q}&top_k=2&format=json",
        token))
    check("?format=json wraps the same string, does not reshape it",
          envelope.get("text") == mirror_text
          and envelope.get("tool") == "search",
          detail=str(sorted(envelope)))

    # The private surface must stay private: no /api route in the schema.
    schema = json.loads(_get("/openapi.json", token))
    api_paths = [p for p in schema.get("paths", {}) if p.startswith("/api")]
    check("no /api route is published in OpenAPI", not api_paths,
          detail=str(api_paths))
    check("OpenAPI declares a security scheme (Authorize works in /docs)",
          bool(schema.get("components", {}).get("securitySchemes")),
          detail=str(schema.get("components", {}).keys()))

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
