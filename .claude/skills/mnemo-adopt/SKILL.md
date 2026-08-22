---
name: mnemo-adopt
description: >
  Adopt mnemo (curated-markdown project memory + a local rebuildable
  vector index) into a project, and set that project up to run as an
  agent team. The mechanical work is done by two commands that own it —
  the installer for the machine, `mnemo init` for the project — and this
  skill does only what a command cannot: triaging an existing CLAUDE.md
  into four buckets (per-role behavior → agents, universal rules →
  .claude/rules/, durable facts → .claude/memory/, orchestration → a
  rewritten clean team-lead CLAUDE.md), the starter agent roster with
  mandatory `memory: project`, the experimental agent-team flag,
  per-agent memory stubs, and migrating a project's user-scope built-in
  Claude memory. Never edits a human-authored file blind. Never commits.

  Use when the user asks to: adopt/set up/init/bootstrap mnemo in a
  project, add shared searchable memory, wire mnemo MCP, set the project
  up as an agent team / team lead, migrate built-in Claude memory into
  the project. Triggers on: "adopt mnemo", "set up mnemo", "init mnemo",
  "mnemo в проект", "підключи mnemo", "налаштуй команду агентів",
  "team lead setup", "bootstrap project memory".
---

# mnemo adopt

Bring a project under **mnemo** and the **team-lead** working model:
curated markdown in git is the single source of truth; a local,
disposable, rebuildable index makes it searchable; the main session plans
and delegates to a team of teammate agents.

## What this skill is for, and what it is not

**`mnemo init` owns the wiring completely.** It registers the bank, mints
its token, builds the whole MCP layer, keeps the memory rule current, and
explains every non-obvious thing it does. Do not describe that wiring from
memory, do not hand-author any part of it, and do not work around anything
it refuses — read what it printed and relay it.

This skill exists for the four things no command can decide:

1. redistributing an existing `CLAUDE.md` into a clean team-lead file;
2. the agent roster, and `memory: project` on every agent;
3. the experimental agent-team flag;
4. migrating user-scope built-in Claude memory into the project.

Everything here is done with a **shown diff and explicit confirmation**,
and **nothing is ever committed** — the user reviews and commits.

## Mental model (details in `references/mnemo.md`)

- **Engine** — user scope, one per machine, not in git. Installed and
  updated by `install.ps1` / `install.sh`.
- **Wiring** — per project, written by `mnemo init`. The git-tracked half
  (`.mcp.json.template`, `.mcp.env.example`, `mcp-setup.sh`,
  `mcp-setup.ps1`, the memory rule, `MEMORY.md`) ships to everyone who
  clones; the token half (`.mcp.env`, and the `.mcp.json` built from it)
  is git-ignored, because it is a live credential.
- **The binding memory rule** is `.claude/rules/mnemo-memory.md`. It
  auto-loads for the main session **and every subagent** (subagents do not
  inherit `CLAUDE.md`, but they do load `.claude/rules/`). That is why the
  rule — not `CLAUDE.md` — carries the memory discipline. `mnemo init`
  owns its text and refreshes it when it is still byte-for-byte one that
  mnemo wrote; an edited file is left alone.
- **`CLAUDE.md`** carries the **team-lead role** (main session only): plan
  and delegate, do not implement. `mnemo init` never touches it — that is
  this skill's job.
- **Banks are flat.** One bank per memory root; `path_prefix` narrows a
  search but is navigation, not isolation. Real isolation is a separate
  bank with its own token. The default for an adopted project is **one
  bank at `.claude/memory`** — ask before proposing anything else.
- A subagent's `memory: project` is its *built-in* per-agent memory at
  user scope. The git-shared curated layer is
  `.claude/memory/agents/<role>/`. Both matter; do not conflate them.

## Workflow

### Step 1 — Inspect (change nothing)

```powershell
# native Windows
& "$HOME\mnemo\bin\mnemo.exe" doctor
Get-ChildItem CLAUDE.md, .mcp.json, .mcp.json.template, .claude -Force -EA SilentlyContinue
Get-ChildItem .claude\rules, .claude\memory, .claude\agents -Force -EA SilentlyContinue
```
```bash
# Linux / macOS
~/.mnemo/bin/mnemo doctor
ls -la CLAUDE.md .mcp.json .mcp.json.template .claude 2>/dev/null
ls -la .claude/rules .claude/memory .claude/agents 2>/dev/null
```

`doctor` answers the engine questions in one shot — installed? model
warmed? service up? — and its `project wiring` section names projects on
this machine whose wiring needs rewiring, with the exact command for each.
If it reports this project there, that command is the one to run in Step 3.

Then read the project itself and note:

- **`CLAUDE.md`** — present? does it already carry a team-lead section?
- **`.claude/memory/`** — absent / a bare anchor / curated? Is it one root
  (`logs/`, `topics/`, `agents/<role>/` nested inside), or the old split
  with `.claude/agent-memory/` beside it (→ offer the move, show the
  mapping, never move files silently)?
- **Agents** — for each `.claude/agents/*.md`, read the frontmatter
  `memory:`. Note every agent whose value is not `project`.
- **Team flag** — is `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` set in any
  `settings.json` `env`?
- **User-scope built-in memory** for this project (slug — see
  `references/memory-migration.md`): present (project / per-agent)?
- Whether the repo carries a lot of markdown unrelated to memory — say so
  plainly if a bank at the project root is ever being considered.

Build a findings block (engine / wiring / CLAUDE.md / memory / agents /
team-flag / user-scope-memory).

### Step 2 — Ask the user

**MANDATORY — use `AskUserQuestion`, never assume, never default.** Ask
only what the findings make relevant.

- **Required (state plainly that they are mandatory):** the clean
  team-lead `CLAUDE.md` (when one exists, its content is triaged into four
  buckets and the file is rewritten — you show the mapping table and the
  full diff and let the user adjust per chunk, but the end state is not
  negotiable); `memory: project` on every subagent. The *content* is
  confirmed; the *requirement* is not.
- **Insist** (explain it is needed for the team model to work at all, then
  take the decision): the `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` flag in
  the project `.claude/settings.json` `env`.
- **Genuinely optional (default = none, never pre-selected):** migrating
  user-scope built-in memory — present the file list; which starter agents
  to add if none exist (default roster: planner, developer, tester,
  reviewer).
- Engine missing or model not warmed → run the installer now?

If `AskUserQuestion` is unavailable, stop, print the findings and the
decisions, and ask for an interactive re-run. Do not assume.

### Step 3 — Run the commands

Only what was approved, in order:

```powershell
# native Windows (PowerShell 5.1+)
& .\install.ps1                                      # if the engine is missing
& "$HOME\mnemo\bin\mnemo.exe" init
```
```bash
# Linux / macOS
./install.sh                                         # if the engine is missing
~/.mnemo/bin/mnemo init
```

The installer does the whole machine in one command — venv, deps,
launcher, token, autostart, the model (it asks), the service, and `doctor`
last. Do not run `warmup` or `service start` separately unless it skipped
them (it does skip the model prompt when the run is not interactive, and
prints the command).

Add **`--migrate`** when Step 1 or `doctor` found an older shape: a stdio
entry, a hook mnemo used to write, or a URL still carrying `/mcp/<bank>`.
A plain `init` will not rewrite a legacy form unasked.

**Read `init`'s output rather than assuming what it did.** It reports what
it added, and three kinds of line matter:

- a **question** — a tracked `.mcp.json` or `.mcp.env` is a live token
  about to be committed, so `init` explains, asks, and runs
  `git rm --cached` itself. Let the user answer it. `--yes` is for scripts;
  with no terminal it does nothing and prints the command. Never write the
  entry by hand to get past it — the token has to come from the registry.
- a **`NOTE`** — part of the wiring needs a hand-finish. Relay it, do not
  swallow it.
- **`refused — …`** — it wrote **nothing**. Carry the found/expected
  detail to Step 4 and re-run once resolved.

`init` then prints the regeneration command. Run it, because until it does
there is no `.mcp.json` at all:

```powershell
powershell -NoProfile -File .\mcp-setup.ps1
```
```bash
bash mcp-setup.sh
```

### Step 4 — Judgement (always a shown diff, never blind)

1. **CLAUDE.md — triage, redistribute, rewrite (always, when one
   exists).** No `CLAUDE.md` → create it from
   `templates/CLAUDE.section.md`. If one exists, do **not** merely append:
   split it into chunks and classify each into exactly one bucket —
   - **per-role behavior** (how to code / test / review / plan) → the
     matching agent (`developer`/`tester`/`reviewer`/`planner`);
   - **universal rule** for everyone (git / commit / style / bans) →
     `.claude/rules/<topic>.md`;
   - **durable project fact** (stack, deploy, conventions) →
     `.claude/memory/` topic files, curated, with `MEMORY.md` staying a
     thin index (same curation rules as `references/memory-migration.md`);
   - **orchestration / lead behavior** → the new `CLAUDE.md`.

   Present a **mapping table** (chunk → bucket → target) **and** the full
   `CLAUDE.md` before/after diff. Classification is judgement: let the user
   adjust per chunk before anything is written. Merging into an existing
   agent or rule file is additive and confirmed per chunk. Apply only on
   confirmation, then replace `CLAUDE.md` with the clean team-lead role —
   the original stays in git history. Never silently drop a chunk; if one
   fits nowhere, ask.
2. **Wiring conflict.** For whatever `init` refused, show `found:` vs the
   `expected:` **copied from the refusal report** (never hand-authored),
   apply, and re-run `init`. A legacy mnemo entry is not yours to edit —
   re-run with `--migrate`.
3. **Subagent memory (mandatory `project`).** For every agent whose
   `memory:` is not `project`, show the frontmatter edit as a diff and
   apply — required, not declinable, but never flipped silently. If the
   project has no agents, create the approved roster from
   `templates/agents/{planner,developer,tester,reviewer}.md` (or
   `templates/agent.md.template` for an extra role), seeded with whatever
   chunks item 1 assigned to that role.
4. **Team flag.** If approved, additively set
   `{"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}}` in the project
   `.claude/settings.json` — merge into any existing `env`, touch no other
   key, show the diff.
5. **Memory migration.** Per `references/memory-migration.md`: curate the
   chosen user-scope files into `.claude/memory/` (thin index + topic files
   + `logs/`) and per-agent notes into `.claude/memory/agents/<role>/`.
   Show the source→target mapping and what is dropped (session state,
   noise) before writing.

### Step 5 — Per-agent memory stubs

Only now that the roster is fixed: for each agent `<role>`, if
`.claude/memory/agents/<role>/MEMORY.md` is absent, create it from
`templates/agent-memory.md.template` (a single heading). Do not create
empty `logs/` or any other structure.

### Step 6 — Verify by looking, not by assuming

```powershell
(Select-String -Path .mcp.json -SimpleMatch '{{').Count    # MUST be 0
Get-Content .mcp.json -Raw | ConvertFrom-Json | Out-Null; "mcp JSON OK"
& "$HOME\mnemo\bin\mnemo.exe" status
& "$HOME\mnemo\bin\mnemo.exe" search "architecture" --bank "$PWD\.claude\memory"
```
```bash
grep -c '{{' .mcp.json                                     # MUST be 0
python3 -m json.tool .mcp.json > /dev/null && echo "mcp JSON OK"
~/.mnemo/bin/mnemo status
~/.mnemo/bin/mnemo search "architecture" --bank "$PWD/.claude/memory"
```

- `.mcp.json` holds one `mnemo-memory` entry, `"type": "http"`, a URL of
  the form `http://<host>:<port>/mcp?token=<48 hex chars>` — **no `{{…}}`
  left**, no `/mcp/<bank>` segment.
- `.mcp.json` and `.mcp.env` are git-ignored and untracked.
- `.claude/rules/mnemo-memory.md` present; `CLAUDE.md` is the clean
  team-lead role with its old content redistributed; every agent
  `memory: project`; team flag set if approved.
- `mnemo status` lists the project's bank as `ready` with a non-zero chunk
  count. There is no separate ingest step — `init` queues the first index
  and the watcher keeps it current. If the model is not warmed it says so
  instead; report that rather than treating an unbuilt index as success.

`search` and `tree` take `--bank` (an id, a name or a path). `--root`
belongs to `init`.

### Step 7 — Tell the user the next steps (never commit)

```
✓ Engine:    <installed | present>  model <warmed | not warmed>  service <up>
✓ Bank:      <name> — registered, index <ready | building>
✓ Wiring:    .mcp.json.template + .mcp.env.example + mcp-setup.sh/.ps1 (git)
             .mcp.env + .mcp.json — git-ignored (bank token)
✓ Rule:      .claude/rules/mnemo-memory.md (binding, lead + all subagents)
✓ CLAUDE.md: clean team-lead (old content redistributed: agents/rules/memory)
✓ Agents:    planner/developer/tester/reviewer — memory: project
✓ Team:      CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 (if approved)
✓ Memory:    one-line anchor [+ migrated N files]

Review the diffs above, then commit — your message, your call.
Do NOT add .mcp.json or .mcp.env: they hold this bank's live token and are
git-ignored on purpose. Anyone cloning gets their own token from the
console (`mnemo ui`) into .mcp.env and runs mcp-setup.
Then trust the project when Claude Code prompts (MCP) and restart the
session so MCP, the rule and the agent team load. Nothing is injected
automatically — memory is reached by calling `search`.
```

Tailor the file list to what actually changed. Never run `git add` or
`git commit` yourself.

## Edge cases

- **Engine absent and the mnemo repo unavailable** — explain the two-layer
  model and stop before wiring; wiring without an engine is inert.
- **Already wired and current** — `init` is a no-op; go straight to
  CLAUDE.md / agents / team-flag / migration.
- **A teammate cloned the repo and MCP does not connect** — expected: the
  token is not in git. They copy their machine's token for that bank from
  the console (`mnemo ui`) into `.mcp.env` and re-run mcp-setup. Never
  paste a token into a git-tracked file to shortcut this.
- **The project already has other MCP servers** — `init` carries them into
  the template rather than losing them, and names them when it does. Their
  values are copied verbatim: if one of them is a secret, that is the
  user's call to move into `.mcp.env`, and worth pointing out.
- **Agent deliberately on non-project memory** — still required to be
  `project` here; explain why (shared team memory) and apply with the diff.
- **Foreign hooks / settings keys** — every merge is additive; never
  reorder or rewrite foreign entries.
- **No `CLAUDE.md` and the user resists** — the team-lead `CLAUDE.md` is
  mandatory; without it the main session has no standing instruction to act
  as lead. Create it, with the diff shown.
- **An already-lean `CLAUDE.md`** — still triage, but the mapping may be
  mostly "→ team-lead"; keep the rewrite minimal and show that little
  changed rather than forcing redistribution.
- **`AskUserQuestion` unavailable** — stop and request an interactive
  re-run; never proceed on assumptions.

## Reference files

- `references/mnemo.md` — the two layers, the data flow, the binding-rule
  mechanism, the team-lead model, the team flag.
- `references/memory-migration.md` — user-scope slug and paths, reading a
  subagent's `memory:`, curation rules when migrating memory in.
- `templates/CLAUDE.section.md` — the mandatory team-lead section.
- `templates/agents/{planner,developer,tester,reviewer}.md` — starter
  teammates (each `memory: project`).
- `templates/agent.md.template` — generic base for an extra teammate.
- `templates/agent-memory.md.template` — the one-line per-agent stub.
