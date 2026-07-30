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

## Banks are flat (v3, already landed)

A **bank** is one root folder of `.md`, anywhere on disk; everything
`*.md` below it is a single index (minus `.git`, `.venv`,
`node_modules`, `__pycache__`). There are **no internal scopes** — the
v2 `project` / `agent` split is gone from the schema, the walk, the CLI
and the MCP tool. `memory_search(query, path_prefix, top_k)` and `mnemo
search --path-prefix` narrow a search to a subfolder at any depth, which
is a **navigation** convenience; the only real isolation boundary is a
**separate bank** with its own MCP connection. Which is why
`.claude/memory/agents/<role>/` still organises per-role notes but no
longer walls them off.

## Two layers

**Engine — user scope, once per machine, NOT in git**
`~/.claude/mnemo/`: `.venv`, `model-cache` (~2.2 GB, only via an
explicit `warmup`), `state/<bankhash>.db` (the disposable index — one
file per **bank root**, keyed by `sha1` of that path), `bin/mnemo`
(self-locating launcher; a real `bin\mnemo.exe` on Windows).
Installed by `install.sh` on POSIX and `install.ps1` on native Windows
(PowerShell 5.1+, 64-bit Python 3.10+ — no WSL, PowerShell 7 or PATH
entry required); idempotent; never deletes `state/` or `model-cache/`.

**Wiring — git scope, per project, ships to everyone who clones**
Created by `mnemo init` — additive, idempotent, refuses rather than
overwrite, never touches `CLAUDE.md`, never invents memory:

- `.mcp.json` — registers the `mnemo` MCP server (portable form).
- `.claude/settings.json` — **untouched unless a hook seed is asked for**
  (`--with-memory-hook` / `--with-inject-hook`, portable shell form).
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
<agent>/memory/`) — not git, not the project's memory root. The
git-shared curated layer is `.claude/memory/agents/<role>/`, driven by
the binding rule + the agent's instructions; the watcher indexes writes
there on its own, with no hook involved. The adopt skill requires `memory:
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

## The hooks — seeds, and nothing is wired for you

`mnemo init` writes **no hook**. Memory is reached by calling
`memory_search`; the discipline lives in `.claude/rules/mnemo-memory.md`,
which loads for the team lead and every subagent. Two seeds exist and are
wired only on request:

- **`--with-memory-hook` → SessionStart → `mnemo memory-hook`** — injects
  the bank's `MEMORY.md` and the folder layout. A **map**, which tells an
  agent to go and look; it cannot be mistaken for having searched. Reads
  the file off disk, so it works with the service down and cannot hang a
  session start. This is the recommended one, and it replaces the native
  `MEMORY.md` auto-load that a bank inside the repo does not get.
- **`--with-inject-hook` → UserPromptSubmit → `mnemo hook-inject`** —
  embeds the prompt, gated search, injects the few most relevant sections.
  Best-effort, never blocks. Off by default for a reason worth repeating to
  the user: it delivers memory **before** the task is stated, and an agent
  that sees sections in its context concludes it has already searched.

**The two v2 reindexing hooks are gone.** A watcher inside the service
reindexes on its own; `hook-postedit` is an exit-0 shim and `ingest` a
deprecated alias, so already-adopted projects keep working until their
owner runs `mnemo init --migrate`, which unwires them.

## Portable invocation (cross-platform)

`mnemo init` writes a launcher reference with **no machine path** in
git — identical on Linux, macOS and native Windows:

- **Hooks** use the shell form `~/.claude/mnemo/bin/mnemo <subcmd>` — the
  shell expands `~` per user at run time.
- **`.mcp.json`** `command` is `${HOME}/.claude/mnemo/bin/mnemo` with
  `args: ["mcp"]` — Claude Code substitutes each teammate's own `$HOME`
  at spawn time. **This form is replaced at v3 phase 4** by an HTTP
  entry (`type: "http"`, a loopback URL, `Authorization: Bearer
  ${MNEMO_API_TOKEN}`, and the bank addressed by **name** in a header).
  The rule it obeys does not change — no machine-dependent value ever
  goes into a git-tracked file — only what gets substituted.

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

Nobody types `mnemo` for memory. It is called by the MCP registration,
any wired hook seed, and this skill (`install.sh --check`, `mnemo init`,
`warmup`, `reindex`, `search` for verification). Not on `PATH`.

v3 softens this in one direction only: the service commands
(`service start|stop|status|restart`), `doctor` and `ui` are meant for a
person. The memory commands stay machine-driven.
