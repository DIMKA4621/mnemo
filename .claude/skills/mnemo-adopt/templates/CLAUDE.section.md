## Project memory (mnemo)

This project uses **mnemo** for shared, searchable memory. The curated
markdown under `.claude/` is the single source of truth; the search
index is local, disposable, and kept current automatically by hooks.

Before non-trivial work, search the memory for prior decisions,
architecture and known pitfalls — use the `mnemo` MCP tool
`memory_search` (scope `project`, and your agent scope when relevant).
Do not re-investigate what is already recorded.

After significant work or a decision, record it so it is not lost:

- general project knowledge → `.claude/memory/` — keep `MEMORY.md` a
  thin index, put detail in topic files, append day notes under `logs/`;
- agent-specific knowledge → `.claude/agent-memory/<agent>/`.

Edit only the `.md` files. The `.md` in git is the source of truth; the
index is derived and rebuilt from it — never edit the index database.
Reindex is automatic: a full reconcile on session start, incrementally
when a memory file is edited, and relevant memory is surfaced into
context on each prompt. The `mnemo` tool `memory_reindex` refreshes the
index on demand.
