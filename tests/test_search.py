"""Labeled recall evaluation (Memory-design-v2: versioned top-k checks).

No pytest / pytrec_eval dependency — a plain runnable script.
Metrics: top-1 accuracy, recall@3, recall@5, plus path_prefix purity.
Exit code 0 = metrics above the regression floor.

Self-contained: reindexes the bundled fixture corpus, then evaluates.
    .venv/bin/python tests/test_search.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import resolve  # noqa: E402
from src.index import reindex  # noqa: E402
from src.search import search  # noqa: E402
from src.store import connect  # noqa: E402

# The labeled corpus is a bundled fixture (a synthetic "demo project"),
# NOT this repo's own memory — mnemo is the framework, it keeps no
# in-repo memory and wires no hooks into itself.
FIXTURE = str(Path(__file__).resolve().parent / "fixtures")
M = ".claude/memory/"
A = ".claude/agent-memory/"


@dataclass
class Case:
    query: str
    expected: set[str]                 # any of these in top-k counts as a hit
    prefix: str | None = None          # v3: path_prefix replaces scope/agent
    note: str = ""


# Regression floor — tune as the corpus grows.
FLOOR = {"top1": 0.70, "recall@3": 0.85, "recall@5": 0.90}

CASES: list[Case] = [
    # --- concrete UA, single clear target ---
    Case("як деплоїмо на продакшн і робимо rollback", {M + "deployment-notes.md"}),
    Case("rollback strategy", {M + "deployment-notes.md"}),
    Case("connection pooling PgBouncer ліміт конектів", {M + "database.md"}),
    Case("як робити міграції бази безпечно", {M + "database.md"}),
    Case("JWT токени і ротація ключів", {M + "security.md"}),
    Case("де зберігати секрети застосунку", {M + "security.md"}),
    Case("rate limiting налаштування", {M + "security.md"}),
    Case("структура логів і request_id", {M + "observability.md"}),
    Case("які метрики збираємо в prometheus", {M + "observability.md"}),
    Case("єдиний формат помилок API", {M + "api-contracts.md"}),
    Case("курсорна чи offset пагінація", {M + "api-contracts.md"}),
    Case("як підняти проєкт локально", {M + "onboarding.md"}),
    Case("worker service фонова обробка задач", {M + "architecture.md"}),
    # --- EN ---
    Case("cache invalidation on write", {M + "caching.md"}),
    Case("dependency and docker image scanning in CI", {M + "security.md"}),
    # --- near-confusable: multiple legitimately relevant files ---
    Case("memory leak у воркері та як полагодили",
         {M + "logs/2026-05-16.md", M + "caching.md"}, note="leak: log + caching"),
    Case("cache stampede mitigation",
         {M + "caching.md", M + "logs/2026-05-14.md"}, note="design + incident"),
    Case("чому CREATE INDEX CONCURRENTLY на проді",
         {M + "database.md", M + "logs/2026-05-12.md"}, note="policy + incident"),
    # --- general task paragraphs ---
    Case("Мені дали задачу: винести важку обробку запитів у фон, щоб не "
         "блокувати API-шлюз. Куди це класти в архітектурі і на що зважати "
         "з памʼяттю?", {M + "architecture.md"}, note="paragraph -> arch"),
    Case("Готую реліз із міграцією бази. Який порядок дій, щоб не покласти "
         "прод?", {M + "database.md", M + "deployment-notes.md",
                   M + "logs/2026-05-12.md"}, note="paragraph -> db/deploy"),
    # --- narrowed by path_prefix (also checked for prefix purity) ---
    Case("стратегія тестування і flaky тести", {A + "tester/MEMORY.md"},
         prefix=A + "tester"),
    Case("що рев'ювер завжди вимагає", {A + "reviewer/MEMORY.md"},
         prefix=A + "reviewer"),
    Case("coding conventions та підводні камені", {A + "developer/MEMORY.md"},
         prefix=A + "developer"),
]


def evaluate() -> int:
    reindex(FIXTURE, verbose=False)  # self-contained: build the fixture index
    n = len(CASES)
    top1 = r3 = r5 = 0
    prefix_checked = prefix_pure = 0
    rows: list[str] = []

    for c in CASES:
        conn = connect(resolve(FIXTURE).db, ensure=False)
        try:
            hits = search(conn, c.query, path_prefix=c.prefix, top_k=5)
        finally:
            conn.close()
        paths = [h.path for h in hits]
        hit1 = bool(paths) and paths[0] in c.expected
        hit3 = any(p in c.expected for p in paths[:3])
        hit5 = any(p in c.expected for p in paths[:5])
        top1 += hit1
        r3 += hit3
        r5 += hit5

        purity = ""
        if c.prefix:
            prefix_checked += 1
            ok = all(h.path.startswith(c.prefix + "/") for h in hits)
            prefix_pure += ok
            purity = " path:OK" if ok else " path:LEAK"

        mark = "OK " if hit1 else ("~3 " if hit3 else ("~5 " if hit5 else "MISS"))
        rows.append(
            f"  [{mark}]{purity:>10}  {c.query[:54]:<54}  -> {paths[0] if paths else '<none>'}"
        )

    print("\n".join(rows))
    metrics = {
        "top1": top1 / n,
        "recall@3": r3 / n,
        "recall@5": r5 / n,
    }
    print(f"\nN={n}  "
          f"top1={metrics['top1']:.2f}  "
          f"recall@3={metrics['recall@3']:.2f}  "
          f"recall@5={metrics['recall@5']:.2f}  "
          f"prefix-purity={prefix_pure}/{prefix_checked}")

    failed = [k for k, v in metrics.items() if v < FLOOR[k]]
    if prefix_checked and prefix_pure != prefix_checked:
        failed.append("prefix-purity")
    if failed:
        print(f"BELOW FLOOR: {', '.join(failed)} (floor={FLOOR})")
        return 1
    print("All metrics above floor.")
    return 0


if __name__ == "__main__":
    sys.exit(evaluate())
