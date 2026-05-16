# mnemo

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
| Engine + venv + model cache | `~/.claude/mnemo/` (installed once) |
| `.md` source of truth | per-project `<root>/.claude/memory` + `agent-memory` (git) |
| Index DB | `~/.claude/mnemo/state/<projhash>.db` (per project, gitignored realm) |

## Commands (`mnemo`)

```bash
mnemo warmup                 # one-time explicit ~2.2 GB model download + check
mnemo ingest [--root DIR]    # reconcile a project's .md -> its index
mnemo search "query" [--root DIR] [--scope project|agent] [--agent NAME]
mnemo mcp                    # stdio MCP server: tools memory_search / memory_reindex
```

## Project-level wiring (everything project-scoped)

Committed in this repo so it travels with the project:

- `.mcp.json` — registers the `mnemo` MCP server (Claude Code spawns
  `mnemo mcp` over stdio per session; not a daemon). Project root =
  cwd, so it searches *this* project's memory.
- `.claude/settings.json` — three reindex triggers:
  - `SessionStart` → `mnemo ingest` (full reconcile; catches
    external changes like git pull);
  - `PostToolUse` (Edit|Write|MultiEdit) → `mnemo hook-postedit`,
    which reads the hook JSON and reconciles **only if the edited file
    is under `.claude/memory` / `.claude/agent-memory`** — unrelated
    edits never touch the DB (process just starts and exits);
  - `SessionEnd` → `mnemo ingest` (final reconcile on close;
    best-effort — SessionStart covers a missed one anyway).

`$MNEMO_ROOT` overrides the project root (used by the standalone
`tests/test_mcp.py` client, which proves the MCP layer without touching
live Claude Code config).

`--root` defaults to the current directory, so the SessionStart hook
indexes whatever project the session runs in. The model is **never**
downloaded implicitly by a hook: `ingest` refuses (with a clear message)
if changes need the model but `warmup` was never run.

## Dev repo vs install

This repo is the **source**. The user-scope copy under
`~/.claude/mnemo/` is the **installed engine** (a packaging script
will automate the copy later). Tests run against the source:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
mnemo warmup
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
