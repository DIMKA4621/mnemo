"""Standalone MCP test client — proves the agent-callable layer.

Spawns `mnemo mcp` over stdio (exactly how a client would), lists
tools, calls memory_search + memory_reindex, asserts. Touches NO live
Claude Code config. Exit 0 = pass.

    .venv/bin/python tests/test_mcp.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Search the bundled fixture corpus (mnemo keeps no in-repo memory).
FIXTURE = str(Path(__file__).resolve().parent / "fixtures")
LAUNCHER = str(Path.home() / ".claude" / "mnemo" / "bin" / "mnemo")

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


async def main() -> int:
    params = StdioServerParameters(
        command=LAUNCHER,
        args=["mcp"],
        env={**os.environ, "MNEMO_ROOT": FIXTURE},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            check("tools exposed", {"memory_search", "memory_reindex"} <= tools,
                  detail=str(sorted(tools)))

            # Build the fixture index first — no hooks wire mnemo into
            # this repo, so the test reconciles explicitly.
            r = await session.call_tool("memory_reindex", {})
            txt = _text(r)
            check("reindex tool runs", "reconcile done" in txt, detail=txt[:120])

            r = await session.call_tool(
                "memory_search",
                {"query": "як деплоїмо на прод і робимо rollback", "top_k": 2},
            )
            txt = _text(r)
            check("search top result = deployment-notes",
                  txt.splitlines()[0].find("deployment-notes.md") != -1,
                  detail=txt[:120])

            r = await session.call_tool(
                "memory_search",
                {"query": "що рев'ювер завжди вимагає",
                 "scope": "agent", "agent": "reviewer", "top_k": 1},
            )
            txt = _text(r)
            check("scoped search isolated to reviewer",
                  "agent-memory/reviewer/" in txt and "/developer/" not in txt,
                  detail=txt[:120])

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
