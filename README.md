# memory-poc

Proof-of-concept for the **shared project memory** (Memory-design-v2).

```
.md  →  chunks  →  embeddings  →  sqlite-vec  →  search
```

One engine, project-agnostic, installed once at **user scope** and used
by every project. The `.md` under a project's `.claude/` is the single
source of truth (git); the index DB is disposable and rebuildable.

## Layers

| Layer | Where |
|---|---|
| Engine + venv + model cache | `~/.claude/memory-poc/` (installed once) |
| `.md` source of truth | per-project `<root>/.claude/memory` + `agent-memory` (git) |
| Index DB | `~/.claude/memory-poc/state/<projhash>.db` (per project, gitignored realm) |

## Commands (`mem-index`)

```bash
mem-index warmup                 # one-time explicit ~2.2 GB model download + check
mem-index ingest [--root DIR]    # reconcile a project's .md -> its index
mem-index search "query" [--root DIR] [--scope project|agent] [--agent NAME]
mem-index mcp                    # stdio MCP server: tools memory_search / memory_reindex
```

## Project-level wiring (everything project-scoped)

Committed in this repo so it travels with the project:

- `.mcp.json` — registers the `memory-poc` MCP server (Claude Code spawns
  `mem-index mcp` over stdio per session; not a daemon). Project root =
  cwd, so it searches *this* project's memory.
- `.claude/settings.json` — `SessionStart` and `PostToolUse`
  (Edit|Write|MultiEdit) hooks both run `mem-index ingest`: full
  reconcile on session start, incremental on memory edits. Editing a
  non-memory file is a ~0.7s no-op (no model load).

`$MEMORY_POC_ROOT` overrides the project root (used by the standalone
`tests/test_mcp.py` client, which proves the MCP layer without touching
live Claude Code config).

`--root` defaults to the current directory, so the SessionStart hook
indexes whatever project the session runs in. The model is **never**
downloaded implicitly by a hook: `ingest` refuses (with a clear message)
if changes need the model but `warmup` was never run.

## Dev repo vs install

This repo is the **source**. The user-scope copy under
`~/.claude/memory-poc/` is the **installed engine** (a packaging script
will automate the copy later). Tests run against the source:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mem-index warmup
python -m src.cli ingest --root .
python tests/test_search.py        # labeled recall eval, exit 0 = pass
```

## Decisions

- **Model:** `intfloat/multilingual-e5-large` (1024-dim) via `fastembed`
  (ONNX, no torch). Documented fallback — fastembed 0.8.0 ships no
  e5-base; same family, one-line swap later.
- **Index is disposable:** delete the project's `state/*.db`, run
  `ingest`, identical state. The `.md` is the only source of truth.
- Vector search primary; FTS5/BM25 secondary; blended via RRF.
