# mnemo

Shared, searchable **project memory** for Claude Code and its agents.

```
.md  →  chunks  →  embeddings  →  sqlite-vec (+ FTS5)  →  search
```

Curated markdown in git is the **single source of truth**. A local,
disposable, rebuildable vector index makes it searchable. No server, no
daemon, nothing in your repo but plain `.md` and a little wiring.

## Quick start

Open the project in Claude Code and tell it:

> adopt mnemo into this project

The `mnemo-adopt` skill takes it from there — installing the engine,
wiring the project, scaffolding the memory rule — showing a diff and
asking before anything non-trivial. It never commits for you.

That's the whole onboarding. From then on the project's memory is kept
indexed and the relevant bits surface on their own.

## How it works

Two layers, cleanly separated:

| Layer | Where | In git? |
|---|---|---|
| Engine (code, venv, model, index) | `~/.claude/mnemo/` — installed once, shared by every project | no |
| Source of truth (`.md`) | `<project>/.claude/memory/` + `.claude/agent-memory/` | **yes** |
| Index DB | `~/.claude/mnemo/state/<projhash>.db` — per project, rebuildable | no |

Adoption commits a tiny bit of git-tracked wiring into the project so it
travels with the repo:

- **`.mcp.json`** — registers the `mnemo` MCP server. Claude Code spawns
  `mnemo mcp` over stdio per session (not a daemon), scoped to this
  project. Tools: `memory_search`, `memory_reindex`.
- **`.claude/settings.json`** — three hooks:
  - `SessionStart` → `mnemo ingest` — full reconcile (catches outside
    changes like a `git pull`).
  - `PostToolUse` (Edit/Write/MultiEdit) → `mnemo hook-postedit` —
    reindexes **only when the edited file is under `.claude/memory` or
    `.claude/agent-memory`**; any other edit is an instant no-op.
  - `UserPromptSubmit` → `mnemo hook-inject` — embeds your prompt,
    searches this project's memory, and surfaces the relevant sections
    into the turn.
- **`.claude/rules/mnemo-memory.md`** — the binding memory rule, loaded
  for the main session and every subagent.

Embedding is served by one warm, idle-exiting helper per machine
(`embed-server`, loopback only) so hooks stay light and CPU stays
bounded. The model is **never** downloaded implicitly — `warmup` is the
only step that fetches it.

The index is disposable: delete a project's `state/*.db`, run `mnemo
ingest`, and you are back to an identical state. The `.md` in git is the
only thing that matters.

## Commands

```bash
mnemo warmup                 # one-time model download + sanity check
mnemo init [--root DIR]      # additive, idempotent project wiring
mnemo ingest [--root DIR]    # reconcile .md -> index (hash-diff + prune)
mnemo search "query" [--scope project|agent] [--agent NAME]
mnemo mcp                    # stdio MCP server (agent tools)
```

`hook-postedit`, `hook-inject` and `embed-server` exist too but are
invoked by the hooks, not by hand. `$MNEMO_ROOT` overrides the project
root; `--root` defaults to the current directory.

## Develop

This repo **is** the system. `install.sh` mirrors `src/` into the
engine home; tests run against the source:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python tests/test_search.py   # labeled recall eval
.venv/bin/python tests/test_mcp.py      # standalone MCP client check
```

Design source of truth: `docs/Memory-design-v2.md` (architecture) and
`docs/Setup-design.md` (install model). Engine: `multilingual-e5-large`
via `fastembed` (ONNX, no torch); vector search primary, FTS5/BM25
secondary, blended with RRF.
