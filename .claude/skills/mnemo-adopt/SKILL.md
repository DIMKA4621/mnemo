---
name: mnemo-adopt
description: >
  Adopt mnemo (curated-markdown project memory + local rebuildable
  vector index) into a project, and set the project up to run as an
  agent team. Orchestrates the deterministic primitives — `install.sh`
  (user-scope engine) and `mnemo init` (additive MCP wiring with the
  bank's own token, kept out of git + a one-line memory anchor + the
  binding memory rule) — and
  handles every judgement call with a shown diff and AskUserQuestion:
  triaging an existing CLAUDE.md into four buckets (per-role behavior →
  agents, universal rules → .claude/rules/, durable facts →
  .claude/memory/, orchestration → a rewritten clean team-lead
  CLAUDE.md), mandatory `memory: project` on subagents, the
  experimental agent-team flag, per-agent memory stubs, resolving
  `mnemo init` conflicts, and migrating a project's user-scope built-in
  Claude memory. Never edits a human-authored file blind. Never commits.

  Use when the user asks to: adopt/set up/init/bootstrap mnemo in a
  project, add shared searchable memory, wire mnemo hooks and MCP, set
  the project up as an agent team / team lead, migrate built-in Claude
  memory into the project. Triggers on: "adopt mnemo", "set up mnemo",
  "init mnemo", "mnemo в проект", "підключи mnemo", "налаштуй команду
  агентів", "team lead setup", "bootstrap project memory".
---

# mnemo adopt

Bring a project under **mnemo** and the **team-lead** working model:
curated markdown in git is the single source of truth; a local,
disposable, rebuildable index makes it searchable; the main session
plans and delegates to a team of teammate agents.

## Status — mnemo is mid-transition to v3 (read before adopting)

The wiring this skill installs is the **current, working** shape. What
changed with v3 phase 4 is now the truth — a project adopted earlier
carries the old shape and needs `mnemo init --migrate`:

- MCP is **HTTP** against the running local service — no per-session
  stdio spawn. `mnemo init --migrate` rewrites legacy wiring; a
  hand-built variant will not be recognised, so do not hand-author one.
- **The URL is `http://127.0.0.1:<port>/mcp?token=<bank-token>` — one
  endpoint, no path segment, no bank name anywhere in it.** Every bank
  is minted its own token when it is registered, and that token is what
  tells the service which bank the connection is for; nothing else does.
  A bank token cannot reach another bank, and rotating it kills the old
  one at once. The superseded `/mcp/<bank>?token=…` form now answers 400
  telling the caller to run `mnemo init --migrate`. If you find yourself
  wanting to add the bank name back "for readability" — do not. What
  identifies the bank to a reader is the `mcpServers` key (`mnemo`,
  `mnemo-notes`); a path component that routing ignores gets read as
  routing by the next person.
- **`.mcp.json` is generated and git-ignored**, because the token in it
  is literal. `mnemo init` adds the `.gitignore` line itself. If the file
  is already tracked, `init` **refuses**: it writes nothing, prints `git
  rm --cached .mcp.json` and exits 1. Run that command (the user's call
  — `init` never runs git), then re-run `init`. Never work around the
  refusal by writing the entry by hand.
- **A project using the template convention gets the placeholder form.**
  Where `.mcp.json.template` exists, git carries it plus
  `.mcp.env.example` and `mcp-setup.sh`, and `.mcp.json` is a build
  product. `init` detects this and writes into that layer instead: the
  entry into the template, the variables into `.mcp.env.example` and
  `.mcp.env`, **and the matching `sed -e` lines into `mcp-setup.sh`**.
  Check that last part in Step 6 rather than assuming it: a missing
  substitution leaves `{{MNEMO_TOKEN}}` sitting in the regenerated
  `.mcp.json` while `mcp-setup.sh` prints its success tick and exits 0,
  so nothing announces the breakage. Someone who clones such a project
  has no token at all (`.mcp.env` is not in git) — they open the cabinet
  (`mnemo ui`), copy the bank's token into `.mcp.env`, and re-run
  `bash mcp-setup.sh`.
- **Two MCP faces, and the tools have no `memory_` prefix.** The project
  face (`/mcp`, bank token) is **read-only**: `search(query, top_k,
  path_prefix)` and `tree`, with no `bank` argument on either. The admin
  face (`/mcp-admin`, service token, server name `mnemo-admin`) carries
  `banks`, `bank_add`, `bank_remove`, `reindex`, `status`, `logs`.
  `reindex` moved there because the watcher already reindexes within
  seconds of a save. Neither token opens the other's face. **Do not wire
  a project's `.mcp.json` at `/mcp-admin`** — that would hand a project
  the credential that reaches every bank on the machine.
- **`init` wires no hooks at all.** The reindexing hooks are gone (the
  watcher does that job) and auto-inject is now opt-in
  (`--with-memory-hook` / `--with-inject-hook`). Memory is reached by
  **searching** — `search` — and the discipline lives in
  `.claude/rules/mnemo-memory.md`. `--migrate` unwires hooks earlier
  versions added.
- **`/mcp-tools/<tool_name>`** mirrors the read tools as plain HTTP for
  hand-checking (Swagger at `/docs`); `/api/*` is the cabinet's private
  channel and is not published.

**Already true today** (v3 phase 0 has landed — this is not a forecast):

- **Banks are flat. Scopes are gone.** `search(query,
  path_prefix, top_k)` takes no `scope`/`agent`, and `mnemo search` has
  `--path-prefix` instead of `--scope`/`--agent`. Everything `*.md` under
  a bank root is **one index**; `path_prefix` narrows a search but is
  navigation, not isolation. Real isolation = a **separate bank**.
- **Per-role notes belong at `.claude/memory/agents/<role>/`**, inside the
  memory root — not at `.claude/agent-memory/` beside it. One root means
  the bank boundary is a folder, so `skills/`, `rules/` and `agents/`
  stay out of the index without any exclusion list. A repo carrying the
  old two-folder layout should be offered the move explicitly (show the
  mapping, do not move files silently). The convention is not an access
  boundary either way — real isolation is a separate bank.
- **A bank root indexes everything below it.** A bank registered at a
  whole project root walks the entire tree for `*.md` (minus `.git`,
  `.venv`, `node_modules`, `__pycache__`), not just `.claude/memory`.
  Tell the user plainly if their repo carries a lot of unrelated markdown
  — and prefer a bank rooted at `.claude/memory`, which is the layout the
  rule teaches and what `mnemo init` registers. There is no separate
  ingest step to run: `init` registers the bank and queues its first
  index, and the watcher keeps it current after that.
- The index schema changed: the first run under the new engine rebuilds
  from the `.md`. Nothing is lost, but a large project's first build is
  not instant.

**How an adopted project is laid out into banks.** The default is **one
bank rooted at `.claude/memory`** — that is the boundary the layout above
exists to create. Per-agent isolation, if wanted, is a **separate bank per
`agents/<role>/` subfolder** with its own MCP connection, never a scope
inside one bank. Ask the user before choosing the second shape.

## The hard boundary (do not cross it)

- **Deterministic primitives do the safe, mechanical work.**
  `install.sh` installs/reports the engine. `mnemo init` creates only
  absent files (a one-line `.claude/memory/MEMORY.md` anchor and the
  binding rule `.claude/rules/mnemo-memory.md`), registers that memory
  root as a bank and mints its token, merges the MCP entry and
  `.claude/settings.json` strictly additively, appends the missing
  `.gitignore` line, and refuses on conflict. They never touch
  `CLAUDE.md`, never overwrite a curated/authored file, never resolve a
  conflict, never invent memory structure, and **never run git** — a
  refusal names the command and stops.
- **This skill + you do every judgement call** — and only with a shown
  diff and explicit confirmation. **Never commit** — the user reviews
  and commits.
- **Single source.** The memory rule text lives in `mnemo init` (it
  writes `.claude/rules/mnemo-memory.md`). Do not author a variant — to
  resolve a conflict, diff against exactly what `mnemo init` writes.

## Mental model (see `references/mnemo.md`)

- **Engine** — user scope, once per machine, not in git.
- **Wiring** — per project: the one-line `MEMORY.md` anchor, the binding
  rule and any `.claude/settings.json` hook seed are git-tracked; the MCP
  entry is **not**, because it carries the bank's token. In a plain
  project that means a git-ignored `.mcp.json`; under the template
  convention the git-tracked `.mcp.json.template` / `.mcp.env.example` /
  `mcp-setup.sh` carry placeholders and the git-ignored `.mcp.env` carries
  the value.
- **The binding memory rule** is `.claude/rules/mnemo-memory.md`. It
  auto-loads for the team lead AND every subagent (subagents do not
  inherit `CLAUDE.md`, but they do load `.claude/rules/`). This is why
  the rule — not `CLAUDE.md` — carries the memory discipline.
- **`CLAUDE.md`** carries the **team-lead role** (main session only):
  plan and delegate, do not implement.
- A subagent's `memory: project` is its *built-in* per-agent memory
  (user scope). The git-shared curated layer is `.claude/memory/agents/
  <role>/`, driven by the rule + instructions + `search`. Both
  matter; do not conflate them. Note the folder is a **convention**, not
  a search scope — see the status section above.

## Workflow

### Step 1 — Inspect

Change nothing. Establish:

**Engine** — check with the installer for the current OS, else inspect
`~/.claude/mnemo/`: installed? model warmed?

```bash
bash <mnemo-repo>/install.sh --check        # Linux / macOS
```
```powershell
# native Windows
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "<mnemo-repo>\install.ps1" -Check
```

**Project** (current dir = root):

```bash
ls -la .mcp.json CLAUDE.md .claude 2>/dev/null
ls -la .mcp.json.template .mcp.env .mcp.env.example mcp-setup.sh 2>/dev/null
ls -la .claude/settings.json .claude/rules .claude/memory \
       .claude/memory/agents .claude/agent-memory .claude/agents 2>/dev/null
git ls-files --error-unmatch .mcp.json 2>/dev/null && echo "TRACKED"
grep -n '^\.mcp\.json$' .gitignore 2>/dev/null
```

Read what is found. Determine:

- **Which shape the project uses**: `.mcp.json.template` present → the
  template convention, and the entry belongs in that layer; absent →
  `init` writes `.mcp.json` directly. Getting this wrong is silent, not
  loud: an entry written straight into `.mcp.json` in a template project
  survives exactly until the next `bash mcp-setup.sh`.
- **Is `.mcp.json` git-tracked?** If the `git ls-files` line printed
  `TRACKED`, `init` will refuse. Surface it in the findings and put `git
  rm --cached .mcp.json` to the user in Step 2 — it is their repository
  and their call, and this skill does not run it for them.
- Wiring: mnemo server present, in the **HTTP** form? Is the URL the
  current `/mcp?token=…` shape, or the superseded `/mcp/<bank>?token=…`
  or an old stdio entry (→ `mnemo init --migrate` in Step 3)? Any hook
  found is legacy — `init` writes none.
- Layout: is memory **one root** (`.claude/memory/` with `logs/`,
  `topics/`, `agents/<role>/`), or the old split with
  `.claude/agent-memory/` beside it (→ offer the move, show the mapping,
  never move files silently)?
- Is `.claude/rules/mnemo-memory.md` present? identical to what `mnemo
  init` writes, or different (→ judgement)?
- `CLAUDE.md`: present? does it already carry a team-lead section?
- `.claude/memory/` — absent / one-line anchor / curated?
- **Agents**: for each `.claude/agents/*.md`, read frontmatter
  `memory:`. Note every agent whose memory is not `project`.
- Team flag: is `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` set in any
  `settings.json` `env`?
- **User-scope built-in memory** for this project (slug — see
  `references/memory-migration.md`): present (project / per-agent)?

Build a findings block (engine / wiring / rule / CLAUDE.md / memory /
agents / team-flag / user-scope-memory).

### Step 2 — Ask the user

**MANDATORY — use `AskUserQuestion`, never assume, never default.** Ask
only what the findings make relevant. Some items are **not optional**
and must be framed as required, not as a yes/no preference:

- **Required (state plainly they are mandatory for mnemo to work):**
  the clean team-lead `CLAUDE.md` (when one exists, its content is
  triaged into 4 buckets and the file is rewritten — you show the
  mapping table + full diff and let the user adjust per chunk, but the
  end state is not negotiable); `memory: project` on every subagent.
  The *content/mapping* is confirmed, the *requirement* is not.
- **Insist (strongly recommend, explain it is needed for the whole
  team to work correctly, then take the decision):** enabling
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` in the project
  `.claude/settings.json` `env`.
- **Genuinely optional (default = none, never pre-select):** migrating
  user-scope built-in memory (project and per-agent) — present the file
  list; which starter agents to add if none exist (default roster:
  planner, developer, tester, reviewer).
- Engine missing / model not warmed → run `install.sh` / `mnemo
  warmup` now?
- **`.mcp.json` found tracked by git** → tell the user plainly that
  `init` refuses until it is untracked, that the file will from now on
  hold a live bank token, and ask them to run `git rm --cached
  .mcp.json` themselves. Do not run it for them, and do not offer to
  write the entry by hand instead.

If `AskUserQuestion` is unavailable, stop, print the findings and the
decisions, ask the user to re-run interactively. Do not assume.

### Step 3 — Run the deterministic primitives

Only what was approved, in order. Use the block for the current OS:

```bash
# Linux / macOS
bash <mnemo-repo>/install.sh              # if engine missing
~/.claude/mnemo/bin/mnemo warmup          # if approved (one-time)
~/.claude/mnemo/bin/mnemo init --root "$PWD"
```
```powershell
# native Windows (PowerShell 5.1+)
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "<mnemo-repo>\install.ps1"                   # if engine missing
& "$HOME\.claude\mnemo\bin\mnemo.exe" warmup        # if approved (one-time)
& "$HOME\.claude\mnemo\bin\mnemo.exe" init --root "$PWD"
```

On a first Windows install `install.ps1` may set the user `HOME` (only
if absent). Relay its instruction precisely: close and reopen the terminal
or IDE that launches Claude Code, then restart Claude Code so it inherits
`HOME`. If installer reports a mismatched existing `HOME`, stop rather than
working around the canonical-path contract.

Add `--migrate` to the `init` line when Step 1 found a mnemo entry in an
older shape — an stdio entry, or a URL still carrying `/mcp/<bank>`.
Without it `init` refuses rather than rewriting what it authored earlier;
with it, it rewrites only its own keys and never a foreign one.

Capture `mnemo init` output. A `refused — …` line means it wrote
**nothing** — expected when an old hardcoded entry exists, or when
`.mcp.json` is git-tracked. Carry the found/expected detail to Step 4 and
re-run `mnemo init` once the conflict is resolved. Read the rest of the
output too: `init` registers the memory root as a bank and reports the
name it got, and a `NOTE` line (a missing `.mcp.env`, an `mcp-setup.sh`
with no recognisable `sed` call) means part of the wiring is incomplete
and needs a hand-finish — relay it, do not swallow it.

### Step 4 — Judgement (always a shown diff, never blind)

1. **CLAUDE.md — triage, redistribute, rewrite (always when it
   exists).** No `CLAUDE.md` → create it from
   `templates/CLAUDE.section.md` (clean team-lead). If one exists, do
   NOT merely append: its content is redistributed so nothing is lost
   and the final `CLAUDE.md` becomes the clean team-lead role only.
   - Split it into chunks; classify each into exactly one bucket:
     - **per-role behavior** (how to code / test / review / plan) →
       the matching agent (`developer`/`tester`/`reviewer`/`planner`);
     - **universal rule** for everyone (git/commit/style/bans) →
       `.claude/rules/<topic>.md`;
     - **durable project fact** (stack, deploy, conventions) →
       `.claude/memory/` topic files — curated, `MEMORY.md` stays a
       thin index (same curation rules as
       `references/memory-migration.md`);
     - **orchestration / lead behavior** → the new `CLAUDE.md`
       (`templates/CLAUDE.section.md`).
   - If a target agent already exists, propose merging its chunks into
     that agent's body — show the diff, confirm per chunk (add or
     skip). If it does not exist, the chunk seeds the starter agent
     created in item 4. An existing `.claude/rules/*` is additive,
     shown, never clobbered.
   - Present a **mapping table** (chunk → bucket → target) AND the full
     `CLAUDE.md` before/after diff. Classification is judgement: let
     the user adjust per chunk before anything is written. Apply only
     on confirmation; then replace `CLAUDE.md` with the clean team-lead
     template. The original is preserved in git history.
   This runs whenever a `CLAUDE.md` exists — even if the project
   already has agents (inspect them and offer the merges). The end
   state is always a clean team-lead `CLAUDE.md`.
2. **Wiring conflict resolution.** For what `mnemo init` refused, show
   `found:` vs the portable `expected:` (copy expected from the refusal
   report — do not hand-author it) and apply in `.mcp.json` /
   `.claude/settings.json`. Re-run `mnemo init` afterwards. Two refusals
   are **not** yours to resolve by editing: a tracked `.mcp.json` (the
   user runs `git rm --cached`, then you re-run `init`) and a mnemo entry
   in an older shape (re-run with `--migrate`). In neither case write the
   entry yourself — the token has to come from the registry, and only
   `init` gets it from there.
3. **`.claude/rules/mnemo-memory.md` conflict.** If it pre-existed and
   differs from what `mnemo init` writes, show the diff and reconcile
   toward the canonical text; never silently overwrite.
4. **Subagent memory (mandatory `project`).** For every agent whose
   `memory:` is not `project`, show the frontmatter edit (`memory:
   project`) as a diff and apply — this is required, not optional;
   never flip silently, but do not present it as declinable. If the
   project has no agents, create the approved starter roster from
   `templates/agents/{planner,developer,tester,reviewer}.md` (or the
   generic `templates/agent.md.template` for extra roles). Generic
   drafts are fine; seed each with whatever chunks item 1 classified to
   that role, otherwise leave the default. Adapt only what the user
   asks.
5. **Team flag.** If approved, additively set
   `{"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}}` in the
   project `.claude/settings.json` — merge into any existing `env`, do
   not touch other keys; show the diff.
6. **Memory migration.** Per `references/memory-migration.md`: curate
   the chosen user-scope files into `.claude/memory/` (thin index +
   topic files + `logs/`) and per-agent notes into
   `.claude/memory/agents/<role>/`. Show the source→target mapping and
   what is dropped (session state / noise) before writing.

### Step 5 — Per-agent memory stubs (after the roster is fixed)

Only now that the agent set is decided, create one-line stubs so agents
do not fabricate structure later. For each agent `<role>`, if
`.claude/memory/agents/<role>/MEMORY.md` is absent, create it from
`templates/agent-memory.md.template` (a single `# <ROLE> agent memory`
heading). Do not create empty `logs/` or other structure.

### Step 6 — Verify

A template project must be regenerated first — until `bash mcp-setup.sh`
runs there is no `.mcp.json` to check, and running it is what proves the
`sed` lines landed.

```bash
# Linux / macOS
bash mcp-setup.sh                             # template projects only
python3 -m json.tool .mcp.json > /dev/null && echo "mcp JSON OK"
python3 -m json.tool .claude/settings.json > /dev/null && echo "settings JSON OK"
grep -c '{{' .mcp.json                        # MUST be 0
~/.claude/mnemo/bin/mnemo status
~/.claude/mnemo/bin/mnemo search "architecture" --bank "$PWD/.claude/memory" | head
```
```powershell
# native Windows (PowerShell 5.1+)
bash mcp-setup.sh          # template projects only; needs Git Bash on PATH
Get-Content .mcp.json -Raw | ConvertFrom-Json > $null; "mcp JSON OK"
Get-Content .claude\settings.json -Raw | ConvertFrom-Json > $null; "settings JSON OK"
(Select-String -Path .mcp.json -SimpleMatch '{{').Count           # MUST be 0
& "$HOME\.claude\mnemo\bin\mnemo.exe" status
& "$HOME\.claude\mnemo\bin\mnemo.exe" search "architecture" `
    --bank "$PWD\.claude\memory" | Select-Object -First 10
```

`search` and `tree` take `--bank` (an id, a name or the bank's path), not
`--root`; `--root` belongs to `init` and to the deprecated `ingest`
alias. There is no separate ingest step: `init` registers the bank and
queues its first index, so `mnemo status` showing the bank `ready` with a
non-zero chunk count is the confirmation.

Confirm, by looking rather than assuming:

- `.mcp.json` holds one `mnemo` entry, `"type": "http"`, URL
  `http://127.0.0.1:<port>/mcp?token=<48 hex chars>` — **no `{{…}}`
  placeholder left in it** and no `/mcp/<bank>` segment. The placeholder
  check is the one that catches the silent failure: `mcp-setup.sh` exits
  0 and prints its success tick whether or not the substitution existed.
- `.mcp.json` is git-ignored and not tracked (`git check-ignore -v
  .mcp.json`). In a template project the same must hold for `.mcp.env`.
- **No hook unless the user asked for a seed**; `.claude/rules/
  mnemo-memory.md` present; `CLAUDE.md` is the clean team-lead role (its
  old content redistributed, nothing lost); every agent `memory:
  project`; team flag set if approved.
- `mnemo status` lists the project's bank as `ready`. If the model is not
  warmed it will say so instead — report that plainly rather than
  treating an unbuilt index as success.

### Step 7 — Tell the user the next steps (never commit)

```
✓ Engine:   <installed | present>  model <warmed | not warmed>
✓ Bank:     <name> — registered, index <ready | building>
✓ Wiring:   .mcp.json (HTTP, bank token, git-ignored) [+conflicts resolved]
            [template project: .mcp.json.template + .mcp.env + mcp-setup.sh]
            hooks: none unless asked
✓ Rule:     .claude/rules/mnemo-memory.md (binding, all agents)
✓ CLAUDE.md: clean team-lead (old content redistributed: agents/rules/memory)
✓ Agents:   planner/developer/tester/reviewer — memory: project
✓ Team:     CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 (if approved)
✓ Memory:   one-line anchor [+ migrated N files]

Review the diffs above, then:
  git add .gitignore .claude/ CLAUDE.md
  # template project, also: .mcp.json.template .mcp.env.example mcp-setup.sh
  git commit        # your message, your call
Do NOT add .mcp.json (or .mcp.env) — they hold this bank's live token and
are git-ignored on purpose. Anyone cloning gets their token from the
cabinet (`mnemo ui`), not from git.
Then trust the project in Claude Code when prompted (MCP) and restart the
session so MCP, the rule and the agent team load. Nothing is injected
automatically — memory is reached by calling `search`.
```

Do not run `git add`/`git commit` yourself. Tailor the `git add` line to
what actually changed — never list a file you did not touch, and never
list `.mcp.json` or `.mcp.env`.

## Edge cases

- **Engine absent and mnemo repo unavailable**: explain the two-layer
  model, stop before wiring (wiring without an engine is inert).
- **Already fully wired (current shape) + rule present**: `mnemo init` is
  a no-op; go straight to CLAUDE.md / agents / team-flag / migration.
- **`.mcp.json` is git-tracked**: `init` refuses and writes nothing. The
  fix is the user's — `git rm --cached .mcp.json`, then re-run `init`.
  Do not hand-write the entry to get past it: the token has to come from
  the registry.
- **Template project with no `.mcp.env`**: `init` writes the template,
  the example and the `sed` lines, but not the real values — it will not
  create a secrets file the project never asked for. Relay its note: `cp
  .mcp.env.example .mcp.env`, then re-run `init`.
- **Template project whose `mcp-setup.sh` has no recognisable `sed …
  "$TEMPLATE"` call**: `init` leaves the script alone and prints the
  lines to add. Show them to the user and let them place them — a
  hand-edited script that then regenerates `.mcp.json` with
  `{{MNEMO_TOKEN}}` still in it fails silently, so verify with the
  placeholder check in Step 6 afterwards.
- **A teammate cloned the repo and MCP does not connect**: expected —
  the token is not in git. They copy their machine's token for that bank
  from the cabinet (`mnemo ui`) into `.mcp.env` (or let `mnemo init`
  write `.mcp.json` for them in a plain project), then restart the
  session. Never paste a token into a git-tracked file to shortcut this.
- **Agent deliberately non-project memory**: still required to be
  `project` here — explain why (shared team memory); apply with the
  diff. The requirement is firm; only the content is shown for review.
- **Foreign MCP servers / hooks / settings keys**: `mnemo init` and the
  team-flag merge are additive — never reorder or rewrite foreign
  entries.
- **No `CLAUDE.md` and the user resists**: the team-lead `CLAUDE.md` is
  mandatory; without it the main session has no standing instruction to
  act as lead or to use the memory. Create it (with the diff shown).
- **Ambiguous / cross-cutting CLAUDE.md chunk**: a chunk that fits more
  than one bucket (e.g. "always run the linters" = developer + tester)
  — do not guess; surface it in the mapping table with your proposed
  split and let the user decide. Never silently drop a chunk; if it
  truly fits nowhere, ask rather than discard.
- **Existing curated CLAUDE.md that is already lean**: still triage,
  but the mapping may be mostly "→ team-lead"; keep the rewrite minimal
  and show that little changed rather than forcing redistribution.
- **`AskUserQuestion` unavailable**: stop and request an interactive
  re-run; never proceed on assumptions.

## Reference files

- `references/mnemo.md` — the two layers, data flow, portable
  invocation, the binding-rule mechanism, the team-lead model, the
  team-flag.
- `references/memory-migration.md` — user-scope slug + paths, reading a
  subagent's `memory:`, curation rules when migrating memory in.
- `templates/CLAUDE.section.md` — the mandatory team-lead section.
- `templates/agents/{planner,developer,tester,reviewer}.md` — starter
  teammates (each `memory: project`).
- `templates/agent.md.template` — generic base for an extra teammate.
- `templates/agent-memory.md.template` — the one-line per-agent stub
  shape used in Step 5.
