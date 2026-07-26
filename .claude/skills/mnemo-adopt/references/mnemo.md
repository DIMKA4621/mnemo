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
`bin/mnemo` (self-locating launcher; a real `bin\mnemo.exe` on Windows).
Installed by `install.sh` on POSIX and `install.ps1` on native Windows
(PowerShell 5.1+, 64-bit Python 3.10+ — no WSL, PowerShell 7 or PATH
entry required); idempotent; never deletes `state/` or `model-cache/`.

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

When a project already has a `CLAUDE.md`, adoption does not append to
it — it **triages** the old content into four destinations so nothing
is lost and the file becomes a clean team-lead role:

- per-role behavior → the matching agent (`developer`/`tester`/
  `reviewer`/`planner`), merged into an existing agent with
  confirmation;
- a universal rule for everyone → `.claude/rules/<topic>.md`;
- a durable project fact → `.claude/memory/` topic files (curated);
- orchestration / lead behavior → the rewritten `CLAUDE.md`.

This runs whenever a `CLAUDE.md` exists (even if agents already exist),
always with a shown mapping table + full diff. Rationale: the common
starting point is a single monolithic `CLAUDE.md` where the main
session is also developer, tester and everything — the team-lead model
needs that "doing" content moved to the roles that own it.

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

## Portable invocation (cross-platform)

`mnemo init` writes a launcher reference with **no machine path** in
git — identical on Linux, macOS and native Windows:

- **Hooks** use the shell form `~/.claude/mnemo/bin/mnemo <subcmd>` — the
  shell expands `~` per user at run time.
- **`.mcp.json`** `command` is `${HOME}/.claude/mnemo/bin/mnemo` with
  `args: ["mcp"]` — Claude Code substitutes each teammate's own `$HOME`
  at spawn time.

The one logical path resolves to the platform's real launcher:
`~/.claude/mnemo/bin/mnemo` (extensionless Bash script) on Linux/macOS,
`bin\mnemo.exe` on Windows (process creation resolves the extensionless
path to the `.exe`). On Windows `install.ps1` sets the user `HOME`
environment variable **only when it is absent** (so `${HOME}` resolves),
never overwrites it, and refuses a value different from PowerShell
`$HOME`/`%USERPROFILE%` so MCP and hooks cannot diverge. After first
creating it, close and reopen the launching terminal or IDE, then restart
Claude Code. Runtime root resolution is the same everywhere — explicit
`--root` > `MNEMO_ROOT` > `CLAUDE_PROJECT_DIR` > cwd — and indexed
relative file identifiers always use `/`, avoiding separator-only drift
between platforms. Resolve a wiring
conflict by copying the portable form from the `mnemo init` refusal
report.

## `mnemo` is not a human command

Nobody types `mnemo`. It is called only by the git-tracked hooks, the
MCP registration, and this skill (`install.sh --check`, `mnemo init`,
`warmup`, `ingest`, `search` for verification). Not on `PATH`.
