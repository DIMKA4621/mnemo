# How mnemo works (reference for the adopt skill)

Use this to explain mnemo accurately and to make correct judgement
calls. It is not user-facing prose — distil from it.

## Single source of truth

Curated markdown in git **is** the memory. The vector index is a
derived, disposable cache.

```
.md  →  chunks  →  embeddings  →  sqlite-vec (+ FTS5)  →  hybrid search
```

The `.md` is authored and reviewed by humans/agents and committed. The
index is rebuilt from it deterministically and is never edited by hand.

## Two layers

**Engine — user scope, once per machine, NOT in git**
`~/.claude/mnemo/`: `.venv` (runtime), `model-cache` (the embedding
model, ~2.2 GB, downloaded only by an explicit `warmup`), `state`
(one `<projhash>.db` per project — the disposable index), `bin/mnemo`
(a self-locating launcher). Installed/refreshed by `install.sh`;
idempotent; never deletes `state/` or `model-cache/`.

**Wiring — git scope, per project, ships to everyone who clones**
`.mcp.json` (registers the `mnemo` MCP server), `.claude/settings.json`
(three hooks), `.claude/memory/` (thin `MEMORY.md` + `logs/` + topic
files), `.claude/agent-memory/<role>/`. Created by `mnemo init` —
additive and idempotent; it refuses rather than overwrite, and never
touches `CLAUDE.md`.

## The hooks

- **SessionStart → `mnemo ingest`** — full reconcile (hash-diff + prune)
  so the index matches the committed `.md`.
- **PostToolUse (Edit|Write|MultiEdit) → `mnemo hook-postedit`** —
  reconciles only when the edited file is inside the memory tree;
  instant no-op otherwise. This is also what captures a subagent's
  memory writes (they go through Edit/Write).
- **UserPromptSubmit → `mnemo hook-inject`** — embeds the prompt via a
  warm resident helper, runs a gated search, and injects the few most
  relevant curated sections into context. Best-effort: if the helper is
  unavailable it skips with a one-line stderr note and never blocks.

There is no SessionEnd hook (it was redundant — PostToolUse already
keeps the index live).

## Portable invocation (why the skill never hand-writes paths)

`mnemo init` writes a launcher reference that carries **no machine
path** into git:

- `.claude/settings.json` hooks use the shell form (a bare `command`
  string, no `args`), so the shell expands `~` at run time — each
  teammate's own `$HOME`.
- `.mcp.json` `command` is a `/bin/sh -c` wrapper
  (`exec "$HOME/.claude/mnemo/bin/mnemo" mcp`) because that field is
  not shell-expanded and `~`/`${HOME}` is not documented there.

This is POSIX (Linux/macOS). A Windows variant is separate, deferred
debt. When resolving a wiring conflict, copy the portable form from the
`mnemo init` refusal report — do not author a variant.

## `mnemo` is not a human command

Nobody types `mnemo`. It is the engine entry point called only by the
git-tracked hooks, the MCP registration, and this skill (`install.sh
--check`, `mnemo init`, `warmup`, `ingest`, `search` for verification).
It is intentionally not on `PATH`.

## The warm embedding helper

Auto-inject would otherwise reload a ~2.2 GB model per prompt. Instead a
single lightweight loopback-TCP resident per machine holds the model
warm, auto-starts on first need, and exits after idle. It is local,
nothing in git, nothing to install, and the model is still only ever
fetched by the explicit `warmup` — consistent with the
no-heavy-infra principle.
