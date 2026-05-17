# How mnemo + the team-lead model work (reference)

Use this to explain accurately and to make correct judgement calls. Not
user-facing prose — distil from it.

## Single source of truth

Curated markdown in git **is** the memory; the vector index is a
derived, disposable cache.

```
.md  →  chunks  →  embeddings  →  sqlite-vec (+ FTS5)  →  hybrid search
```

The `.md` is authored/reviewed by humans and agents and committed. The
index is rebuilt from it deterministically and never edited by hand.

## Two layers

**Engine — user scope, once per machine, NOT in git**
`~/.claude/mnemo/`: `.venv`, `model-cache` (~2.2 GB, only via an
explicit `warmup`), `state/<projhash>.db` (the disposable index),
`bin/mnemo` (self-locating launcher). Installed by `install.sh`;
idempotent; never deletes `state/` or `model-cache/`.

**Wiring — git scope, per project, ships to everyone who clones**
Created by `mnemo init` — additive, idempotent, refuses rather than
overwrite, never touches `CLAUDE.md`, never invents memory:

- `.mcp.json` — registers the `mnemo` MCP server (portable form).
- `.claude/settings.json` — three hooks (portable shell form).
- `.claude/memory/MEMORY.md` — a **one-line anchor** if absent
  (`# Memory Index — <project>`), nothing more.
- `.claude/rules/mnemo-memory.md` — the **binding memory rule** if
  absent. `mnemo init` owns this text; it is the single source.

## The binding rule vs CLAUDE.md (critical)

`.claude/rules/*.md` auto-loads into the **main session AND every
subagent**. Subagents do **not** inherit `CLAUDE.md`, but they do load
`.claude/rules/`. Therefore:

- The **memory discipline** lives in `.claude/rules/mnemo-memory.md`
  (mandatory, universal: lead + all teammates).
- `CLAUDE.md` carries the **team-lead role only** (main session): plan
  and delegate, do not implement. The team-lead section is mandatory
  but is the adopt skill's judgement (shown diff) — `mnemo init` never
  writes `CLAUDE.md`.

## Subagent memory — do not conflate

A subagent's frontmatter `memory: project` enables its **built-in**
per-agent memory at **user scope** (`~/.claude/projects/<slug>/agents/
<agent>/memory/`) — not git, not `.claude/agent-memory/`. The
git-shared curated layer is `.claude/agent-memory/<role>/`, driven by
the binding rule + the agent's instructions + the mnemo PostToolUse
hook (which indexes writes there). The adopt skill requires `memory:
project` on every agent **and** relies on the rule for the git layer —
both, for different reasons.

## The team-lead working model

The main session is the **team lead**: it plans and delegates, it does
not implement or read large amounts of code (keeps its context lean).
Work runs as an **agent team** of teammates that share a task list and
message each other:

- **planner** — explores code, produces/refines the plan (token-heavy
  reading isolated here).
- **developer** — implements the agreed plan.
- **tester** — verifies against the plan.
- **reviewer** — reviews changes, stress-tests plan detail.

Flow: understand → planner plans → agree high-level with the user →
developer implements → tester verifies → reviewer reviews → lead
integrates and reports.

## The team-flag

Agent teams are experimental and **off by default**. They require
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1"` in the `env` block of a
`settings.json` (user or project scope). The adopt skill sets it in the
**project** `.claude/settings.json` `env` (ships to the whole team),
additively, after insisting it is needed for the model to work.

## The hooks

- **SessionStart → `mnemo ingest`** — full reconcile (hash-diff +
  prune).
- **PostToolUse (Edit|Write|MultiEdit) → `mnemo hook-postedit`** —
  reconciles only when the edited file is inside the memory tree;
  instant no-op otherwise. Also captures teammates' memory writes.
- **UserPromptSubmit → `mnemo hook-inject`** — embeds the prompt via a
  warm resident helper, gated search, injects the few most relevant
  curated sections. Best-effort; never blocks. No SessionEnd hook.

## Portable invocation

`mnemo init` writes a launcher reference with **no machine path** in
git: hooks use the shell form (`~` expands per-user at run time); the
`.mcp.json` `command` is a `/bin/sh -c` wrapper expanding `$HOME`. POSIX
(Linux/macOS); Windows is separate deferred debt. Resolve a conflict by
copying the portable form from the `mnemo init` refusal report.

## `mnemo` is not a human command

Nobody types `mnemo`. It is called only by the git-tracked hooks, the
MCP registration, and this skill (`install.sh --check`, `mnemo init`,
`warmup`, `ingest`, `search` for verification). Not on `PATH`.
