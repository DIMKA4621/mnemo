"""Reproducible search assertions (Memory-design-v2: versioned top-k checks).

No pytest dependency — a plain runnable script. Exit code 0 = all pass.

Run from the repo root:
    .venv/bin/python tests/test_search.py

Assumes `python -m src.cli ingest` has been run (memory.db present).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.search import search  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def top_path(query: str, **kw) -> str:
    hits = search(query, **kw)
    return hits[0].path if hits else "<no results>"


# 1. Concrete UA queries land on the right topic file.
check(
    "deploy/rollback -> deployment-notes",
    top_path("як деплоїмо на продакшн і робимо rollback")
    == ".claude/memory/deployment-notes.md",
)
check(
    "memory leak -> day log",
    top_path("memory leak у воркері та як полагодили")
    == ".claude/memory/logs/2026-05-16.md",
)

# 2. Scope filter: project-only never leaks agent docs.
proj = search("rollback strategy", scope="project")
check("project scope top1", proj[0].path == ".claude/memory/deployment-notes.md")
check(
    "project scope is pure",
    all(h.scope == "project" for h in proj),
    detail=str([h.scope for h in proj]),
)

# 3. Scope filter: a single agent's memory is isolated.
tester = search("стратегія тестування і flaky тести", scope="agent", agent_name="tester")
check(
    "tester scope isolated",
    bool(tester)
    and all(h.path.startswith(".claude/agent-memory/tester/") for h in tester),
    detail=str([h.path for h in tester]),
)
check(
    "developer scope top1",
    top_path("coding conventions та підводні камені", scope="agent",
             agent_name="developer")
    == ".claude/agent-memory/developer/MEMORY.md",
)

# 4. A general task-paragraph still retrieves the architecturally right doc.
check(
    "general task paragraph -> architecture",
    top_path(
        "Мені дали задачу: винести важку обробку запитів у фон, щоб не "
        "блокувати API-шлюз. Куди це класти і на що зважати з памʼяттю?"
    )
    == ".claude/memory/architecture.md",
)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
