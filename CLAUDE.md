# CLAUDE.md — mnemo

You are developing **mnemo**: a project-memory system for Claude Code and
agents. This repository IS the system itself (not a project that merely
uses it).

## What mnemo is

Curated markdown in git is the **single source of truth**; a local,
disposable, rebuildable vector index makes it searchable.

```
.md  →  chunks  →  embeddings  →  sqlite-vec (+ FTS5)  →  search
```

- Source of truth: per-project `.claude/memory/` (thin `MEMORY.md` index
  + `logs/` + topic files) and `.claude/agent-memory/<agent>/`.
- Index: one SQLite file per project at `~/.claude/mnemo/state/<projhash>.db`
  — gitignored realm, deletable, fully rebuildable from the `.md`.
- Access: `mnemo` CLI (also the MCP face) + git-tracked project-level
  hooks. No server, no daemon.

## Design source of truth

`docs/Memory-design-v2.md` is the authoritative design. **Read it before
changing architecture.** `docs/Memory-design-v1.md` is historical only.

Project decision history, research and rationale live in Claude Code's
memory for this project (loaded each session as `MEMORY.md`). Read it
before planning — do not re-investigate what is already recorded there.

## Architecture map

- `src/config.py` — paths, model, knobs; per-project resolution.
- `src/chunker.py` — heading-aware markdown splitting.
- `src/embedder.py` — fastembed (ONNX), `multilingual-e5-large`.
- `src/store.py` — sqlite-vec + FTS5 + change-state (hashes in the DB).
- `src/index.py` — walk + sha256-diff + reindex changed + prune.
- `src/search.py` — vector kNN + FTS5 + RRF + scope filter.
- `src/mcp_server.py` — same engine exposed as MCP tools.
- `src/cli.py` — `warmup | ingest | search | mcp | hook-postedit`.
- `tests/test_search.py` — labeled recall eval (regression floor).
- `tests/test_mcp.py` — standalone MCP client check.
- Installed engine: `~/.claude/mnemo/` (`bin/mnemo`, `.venv`,
  `model-cache`, `state/`). Project wiring: `.mcp.json`,
  `.claude/settings.json`. Reusable templates: `templates/`.

## Commands

```
mnemo warmup                  one-time explicit ~2.2 GB model download + check
mnemo ingest [--root DIR]     reconcile .md -> index (hash-diff + prune)
mnemo search "q" [--scope …]  hybrid search over project memory
mnemo mcp                     stdio MCP server (memory_search / memory_reindex)
mnemo hook-postedit           PostToolUse target (acts only on memory files)
```

## Locked decisions (see Claude memory for full rationale)

- Embedding: `multilingual-e5-large` via fastembed (documented fallback
  from e5-base — one-line swap in `src/config.py`).
- Vector search primary; FTS5/BM25 secondary; blended with RRF.
- The index is disposable and rebuilds deterministically from `.md`.
- Hooks live in git-tracked project settings; the model is never
  downloaded implicitly by a hook (explicit `warmup` only).

## Working rules

- **Step-by-step.** Stop and confirm at architectural forks. Never write
  code without explicit approval. Surface unexpected complexity instead
  of pushing through.
- **Conventional Commits.** Ask before committing/pushing.
- **Never** add `Co-Authored-By` or any attribution line.
- Comments and commit messages in English.
- Subagents do NOT inherit this file — any rule a subagent must follow
  belongs in its own agent file (see `templates/agents/`).
