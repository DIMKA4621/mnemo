# How mnemo + the team-lead model work (reference)

Use this to explain accurately and to make correct judgement calls.
Not user-facing prose — distil from it.

## Single source of truth

Curated markdown in git **is** the memory; the vector index is a derived, disposable cache.

```
.md  →  chunks  →  embeddings  →  sqlite-vec (+ FTS5)  →  hybrid search
```

The `.md` is authored/reviewed by humans and agents and committed.
The index is rebuilt from it deterministically and never edited by hand.

## Banks are flat (v3, already landed)

A **bank** is one root folder of `.md`, anywhere on disk; everything `*.md` below it is a single index (minus `.git`, `.venv`, `node_modules`, `__pycache__`).
There are **no internal scopes** — the v2 `project` / `agent` split is gone from the schema, the walk, the CLI and the MCP tool.
`search(query, path_prefix, top_k)` and `mnemo search --path-prefix` narrow a search to a subfolder at any depth, which is a **navigation** convenience; the only real isolation boundary is a **separate bank** with its own MCP connection.
Which is why `.claude/memory/agents/<role>/` still organises per-role notes but no longer walls them off.

## Two layers

**Engine — user scope, once per machine, NOT in git** `~/.mnemo/`: `.venv`, `model-cache` (~2.2 GB, only via an explicit `warmup`), `state/<bankhash>.db` (the disposable index — one file per **bank root**, keyed by `sha1` of that path), `bin/mnemo` (self-locating launcher; a real `bin\mnemo.exe` on Windows).
Installed by `install.ps1` on native Windows (built-in PowerShell 5.1+, 64-bit Python 3.10+ — no WSL, PowerShell 7 or PATH entry required) and `install.sh` on POSIX.
One command does the whole machine: venv, deps, launcher, API token, autostart, the model (it asks, and skips silently when the run is not interactive), the service, and `doctor` last — so the run ends on evidence rather than a claim.
Idempotent, and the update path; never deletes `state/` or `model-cache/`.
`uninstall.ps1` / `uninstall.sh` is its mirror image.

**Wiring — per project, most of it ships to everyone who clones** Created by `mnemo init` — additive, idempotent, refuses rather than overwrite, never touches `CLAUDE.md`, never invents memory structure:

- `.claude/memory/MEMORY.md` — a **one-line anchor** if absent (`# Memory Index — <project>`), nothing more.
  Git-tracked, and never reconsidered afterwards: it is curated content.
- `.claude/rules/mnemo-memory.md` — the **binding memory rule**.
  `mnemo init` owns this text and is its single source.
  It is also **refreshed in place** when the file's bytes still hash to a redaction mnemo itself wrote, so a project adopted months ago does not keep months-old rules with nothing saying so; an unrecognised digest means a person edited it, and it is then left exactly as it is.
  Git-tracked.
- `.claude/settings.json` — **never touched by a plain `init`**.
  Only `--migrate` edits it, and only to remove hooks.
  Git-tracked.
- The MCP layer — one shape, described below.

**The MCP layer, and why the token stays out of git.** `init` registers the project's memory root as a bank; registration mints that bank a token.
The token never goes into a tracked file, so there is exactly one shape, and `init` builds it wherever it is missing:

| File | In git | Holds |
|---|---|---|
| `.mcp.json.template` | yes | the entry, with `{{MNEMO_HOST}}` / `{{MNEMO_PORT}}` / `{{MNEMO_TOKEN}}` |
| `.mcp.env.example` | yes | the variable names, empty token |
| `mcp-setup.sh`, `mcp-setup.ps1` | yes | regenerate `.mcp.json` from the two |
| `.mcp.env` | **no** | the real values, token included |
| `.mcp.json` | **no** | a build product — `init` never writes it |

Three facts worth knowing before explaining any of it:

- **The template starts as the project's existing `.mcp.json`.** Otherwise converting to the layer would drop every other server the project had, silently, one step later.
- **Both scripts discover their substitutions from the template**, so a new bank is an entry plus a token and nothing else.
  This closed the layer's one silent failure: a placeholder with no value used to pass through into the generated `.mcp.json` verbatim while the script printed its success tick and exited 0. It is now a named error and no file written.
  There are two scripts because a native Windows machine has no reason to have bash; a test holds their output byte-identical.
- **A tracked `.mcp.json` or `.mcp.env` is a conversation, not a refusal.** `init` explains why a live token cannot be tracked, asks, and runs `git rm --cached` itself (`--yes` answers for scripts; with no terminal it does nothing and prints the command).
  Never hand-write the entry to get past it — the token has to come from the registry, and only `init` gets it from there.

## Two MCP faces, and which token opens which

The tools carry **no `memory_` prefix** — Claude Code already namespaces them as `mcp__<server>__<tool>`, so a prefix only restated the namespace and cost tokens in every tool description.

- **`/mcp?token=<bank-token>`** — the project face, server key `mnemo-memory`.
  **Read-only, two tools:** `search(query, top_k, path_prefix)` and `tree(path_prefix, depth)`.
  Neither takes a `bank` argument; the bank comes from the token.
  This is what an adopted project is wired to.
- **`/mcp-admin?token=<service-token>`** — the admin face, server name `mnemo-admin` (tools namespace as `mcp__mnemo-admin__reindex`).
  Tools: `banks`, `bank_add`, `bank_remove`, `reindex`, `status`, `logs`.

`reindex` sits on the admin face, not the project one, because the watcher reindexes within seconds of a save — on a project face it would be a tool slot spent in every session on a button almost nobody presses.

The two credentials do not cross, and this is what makes a per-project token safe to hand out: a bank token on `/mcp-admin` is a 401, and the service token on `/mcp` is a 401 too, since it resolves to no bank.
Never wire a project at `/mcp-admin` — that would give one project the credential that reaches every bank on the machine.

For hand-checking there is `/mcp-tools/<tool_name>` (service token, plain HTTP, Swagger at `/docs`), which calls the very same tool bodies and so cannot drift from what an agent reads.
`/api/*` is the console's private channel and is not published.

## The binding rule vs CLAUDE.md (critical)

`.claude/rules/*.md` auto-loads into the **main session AND every subagent**.
Subagents do **not** inherit `CLAUDE.md`, but they do load `.claude/rules/`.
Therefore:

- The **memory discipline** lives in `.claude/rules/mnemo-memory.md` (mandatory, universal: lead + all teammates).
- `CLAUDE.md` carries the **team-lead role only** (main session): plan and delegate, do not implement.
  The team-lead section is mandatory but is the adopt skill's judgement (shown diff) — `mnemo init` never writes `CLAUDE.md`.

When a project already has a `CLAUDE.md`, adoption does not append to it — it **triages** the old content into four destinations so nothing is lost and the file becomes a clean team-lead role:

- per-role behavior → the matching agent (`developer`/`tester`/ `reviewer`/`planner`), merged into an existing agent with confirmation;
- a universal rule for everyone → `.claude/rules/<topic>.md`;
- a durable project fact → `.claude/memory/` topic files (curated);
- orchestration / lead behavior → the rewritten `CLAUDE.md`.

This runs whenever a `CLAUDE.md` exists (even if agents already exist), always with a shown mapping table + full diff.
Rationale: the common starting point is a single monolithic `CLAUDE.md` where the main session is also developer, tester and everything — the team-lead model needs that "doing" content moved to the roles that own it.

## Subagent memory — do not conflate

A subagent's frontmatter `memory: project` enables its **built-in** per-agent memory at **user scope** (`~/.claude/projects/<slug>/agents/ <agent>/memory/`) — not git, not the project's memory root.
The git-shared curated layer is `.claude/memory/agents/<role>/`, driven by the binding rule + the agent's instructions; the watcher indexes writes there on its own, with no hook involved.
The adopt skill requires `memory: project` on every agent **and** relies on the rule for the git layer — both, for different reasons.

## The team-lead working model

The main session is the **team lead**: it plans and delegates, it does not implement or read large amounts of code (keeps its context lean).
Work runs as an **agent team** of teammates that share a task list and message each other:

- **planner** — explores code, produces/refines the plan (token-heavy reading isolated here).
- **developer** — implements the agreed plan.
- **tester** — verifies against the plan.
- **reviewer** — reviews changes, stress-tests plan detail.

Flow: understand → planner plans → agree high-level with the user → developer implements → tester verifies → reviewer reviews → lead integrates and reports.

## The team-flag

Agent teams are experimental and **off by default**.
They require `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1"` in the `env` block of a `settings.json` (user or project scope).
The adopt skill sets it in the **project** `.claude/settings.json` `env` (ships to the whole team), additively, after insisting it is needed for the model to work.

## The hooks — there are none, and no flag brings one back

`mnemo init` writes **no hook**, and no flag makes it write one.
Memory is reached by calling `search`; the discipline lives in `.claude/rules/mnemo-memory.md`, which loads for the team lead and every subagent and is the **only** place that states it.

Two seeds used to exist behind flags.
Both are gone, together with their commands, and it is worth being able to say why if a user asks:

- **`hook-inject` (UserPromptSubmit)** injected the few most relevant sections.
  It delivers memory **before** the task is stated, and an agent that sees sections in its context concludes it has already searched.
- **`memory-hook` (SessionStart)** injected `MEMORY.md` and the layout — a **map**, not answers, which is why it survived longer.
  Removed because the `tree` tool answers exactly that on demand, and a map arriving unasked competes with the rule that says go and look.

The general form: two mechanisms stating one rule can drift, and the one nobody edits wins by accident.

**The two v2 reindexing hooks are gone.** A watcher inside the service reindexes on its own; `hook-postedit` is an exit-0 shim and `ingest` a deprecated alias, so already-adopted projects keep working until their owner runs `mnemo init --migrate`, which unwires them.

## Portable invocation (cross-platform)

Nothing machine-dependent and no secret ever goes into a git-tracked file — identical on Linux, macOS and native Windows.
There are no hooks left to be portable, so this is about one entry:

- **The MCP entry** is `{"type": "http", "url": "http://127.0.0.1:<port>/mcp?token=<bank-token>"}`.
  No `command`, no `args`, no spawn: the session connects to the already-running service.
  No `headers` either — the value rides in the URL because a header depends on Claude Code forwarding `headers` for `type: http`, and if it does not, authentication fails outright.
- **No path segment and no bank name.** The token *is* the address: it belongs to one bank and resolves to it, so a second thing naming the bank could only disagree with the credential — and a request that succeeds against the wrong bank looks entirely normal.
  The `X-Mnemo-Bank` header is gone for the same reason, as is the "if there is only one bank it must be that one" fallback.
  What tells a reader which bank an entry serves is the `mcpServers` key (`mnemo-memory`, `mnemo-notes`).
  `init` writes `mnemo-memory`; an older `mnemo` key it authored is renamed rather than joined, so a project never ends up with two entries into one bank.
- The literal token is the reason `.mcp.json` is git-ignored rather than tracked; git carries `{{MNEMO_HOST}}` / `{{MNEMO_PORT}}` / `{{MNEMO_TOKEN}}` and the values live in `.mcp.env` — a placeholder at every varying position of the URL, the host included, since `MNEMO_API_HOST` has always been configurable.
  Only the token is per-entry: a second bank takes `MNEMO_NOTES_TOKEN` (else it would overwrite the first's), while host and port stay shared, because they address the **service** and a copy per bank is free to disagree about a fact that is single.

The one logical path resolves to the platform's real launcher: `~/.mnemo/bin/mnemo` (extensionless Bash script) on Linux/macOS, `bin\mnemo.exe` on Windows (process creation resolves the extensionless path to the `.exe`).
On Windows `install.ps1` sets the user `HOME` environment variable **only when it is absent**, never overwrites it, and refuses a value different from PowerShell `$HOME`/`%USERPROFILE%` — the engine lives under `HOME`, and the portable form resolves against it.
After first creating it, close and reopen the launching terminal or IDE, then restart Claude Code.
Root resolution for the commands that take one is the same everywhere — explicit `--root` > `MNEMO_ROOT` (a deliberate override for containerized deployments, never set implicitly) > cwd — and indexed relative file identifiers always use `/`, avoiding separator-only drift between platforms.
Resolve a wiring conflict by copying the expected form from the `mnemo init` refusal report.

## `mnemo` is not a human command

Nobody types `mnemo` to *use* memory — that is the MCP `search` tool.
The launcher is called by this skill (`mnemo init`, and `doctor` / `status` / `search` for verification) and by whoever is administering the machine.
Not on `PATH`: call it in full, or alias it.

Note which flag each command takes: `--root` belongs to `init` (and to the deprecated `ingest` alias); the API-client commands — `search`, `tree`, `reindex`, `logs` — address a bank with `--bank`, which accepts an id, a name or the bank's path.
Passing `--root` to `search` is an argparse error, not a silent fallback.

v3 softens this in one direction only: the service commands (`service start|stop|status|restart`), `doctor` and `ui` are meant for a person.
The memory commands stay machine-driven.
