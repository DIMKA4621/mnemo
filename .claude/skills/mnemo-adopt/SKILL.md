---
name: mnemo-adopt
description: >
  Adopt mnemo (curated-markdown project memory + local rebuildable
  vector index) into a project, and set the project up to run as an
  agent team. Orchestrates the deterministic primitives — `install.sh`
  (user-scope engine) and `mnemo init` (git-tracked, additive, portable
  wiring + a one-line memory anchor + the binding memory rule) — and
  handles every judgement call with a shown diff and AskUserQuestion:
  the mandatory team-lead section in CLAUDE.md, mandatory `memory:
  project` on subagents, the experimental agent-team flag, per-agent
  memory stubs, resolving `mnemo init` conflicts, and migrating a
  project's user-scope built-in Claude memory. Never edits a
  human-authored file blind. Never commits.

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

## The hard boundary (do not cross it)

- **Deterministic primitives do the safe, mechanical work.**
  `install.sh` installs/reports the engine. `mnemo init` creates only
  absent files (a one-line `.claude/memory/MEMORY.md` anchor and the
  binding rule `.claude/rules/mnemo-memory.md`), merges `.mcp.json` /
  `.claude/settings.json` strictly additively, and refuses on conflict.
  They never touch `CLAUDE.md`, never overwrite a curated/authored
  file, never resolve a conflict, never invent memory structure.
- **This skill + you do every judgement call** — and only with a shown
  diff and explicit confirmation. **Never commit** — the user reviews
  and commits.
- **Single source.** The memory rule text lives in `mnemo init` (it
  writes `.claude/rules/mnemo-memory.md`). Do not author a variant — to
  resolve a conflict, diff against exactly what `mnemo init` writes.

## Mental model (see `references/mnemo.md`)

- **Engine** — user scope, once per machine, not in git.
- **Wiring** — git-tracked per project: `.mcp.json`,
  `.claude/settings.json` hooks, the one-line `MEMORY.md` anchor, the
  binding rule.
- **The binding memory rule** is `.claude/rules/mnemo-memory.md`. It
  auto-loads for the team lead AND every subagent (subagents do not
  inherit `CLAUDE.md`, but they do load `.claude/rules/`). This is why
  the rule — not `CLAUDE.md` — carries the memory discipline.
- **`CLAUDE.md`** carries the **team-lead role** (main session only):
  plan and delegate, do not implement.
- A subagent's `memory: project` is its *built-in* per-agent memory
  (user scope). The git-shared curated layer is `.claude/agent-memory/
  <role>/`, driven by the rule + instructions + the mnemo hook. Both
  matter; do not conflate them.

## Workflow

### Step 1 — Inspect

Change nothing. Establish:

**Engine** (`bash <mnemo-repo>/install.sh --check` if available, else
inspect `~/.claude/mnemo/`): installed? model warmed?

**Project** (current dir = root):

```bash
ls -la .mcp.json CLAUDE.md .claude 2>/dev/null
ls -la .claude/settings.json .claude/rules .claude/memory \
       .claude/agent-memory .claude/agents 2>/dev/null
```

Read what is found. Determine:

- Wiring: mnemo server + the three hooks present? portable form or an
  old hardcoded path (→ a conflict `mnemo init` will refuse → Step 4)?
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
  the team-lead section in `CLAUDE.md`; `memory: project` on every
  subagent. You still show the diff before applying — the *content* is
  confirmed, the *requirement* is not negotiable.
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

If `AskUserQuestion` is unavailable, stop, print the findings and the
decisions, ask the user to re-run interactively. Do not assume.

### Step 3 — Run the deterministic primitives

Only what was approved, in order:

```bash
bash <mnemo-repo>/install.sh             # if engine missing
~/.claude/mnemo/bin/mnemo warmup          # if approved (one-time)
~/.claude/mnemo/bin/mnemo init --root "$PWD"
```

Capture `mnemo init` output. A `refused — …` line means it wrote
nothing (expected when an old hardcoded entry exists) — carry the
found/expected detail to Step 4 and re-run `mnemo init` after the
conflict is resolved.

### Step 4 — Judgement (always a shown diff, never blind)

1. **CLAUDE.md team-lead section (mandatory).** Use
   `templates/CLAUDE.section.md`. No `CLAUDE.md` → create it with that
   section. Exists → insert/reconcile a `## Team lead (mnemo project)`
   section (do not duplicate; if a team-lead/role section exists,
   reconcile). Show the diff; apply on confirmation of the content.
2. **Wiring conflict resolution.** For what `mnemo init` refused, show
   `found:` vs the portable `expected:` (copy expected from the refusal
   report — do not hand-author it) and apply in `.mcp.json` /
   `.claude/settings.json`. Re-run `mnemo init` afterwards.
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
   drafts are fine; adapt only what the user asks.
5. **Team flag.** If approved, additively set
   `{"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}}` in the
   project `.claude/settings.json` — merge into any existing `env`, do
   not touch other keys; show the diff.
6. **Memory migration.** Per `references/memory-migration.md`: curate
   the chosen user-scope files into `.claude/memory/` (thin index +
   topic files + `logs/`) and per-agent notes into
   `.claude/agent-memory/<role>/`. Show the source→target mapping and
   what is dropped (session state / noise) before writing.

### Step 5 — Per-agent memory stubs (after the roster is fixed)

Only now that the agent set is decided, create one-line stubs so agents
do not fabricate structure later. For each agent `<role>`, if
`.claude/agent-memory/<role>/MEMORY.md` is absent, create it from
`templates/agent-memory.md.template` (a single `# <ROLE> agent memory`
heading). Do not create empty `logs/` or other structure.

### Step 6 — Verify

```bash
~/.claude/mnemo/bin/mnemo ingest --root "$PWD"
python3 -m json.tool .mcp.json > /dev/null && echo "mcp JSON OK"
python3 -m json.tool .claude/settings.json > /dev/null && echo "settings JSON OK"
~/.claude/mnemo/bin/mnemo search "architecture" --root "$PWD" | head
```

Confirm: portable `mnemo` server + three hooks; `.claude/rules/
mnemo-memory.md` present; CLAUDE.md team-lead section present; every
agent `memory: project`; team flag set if approved; index built. If the
model is not warmed, `mnemo ingest` will say so — report that plainly.

### Step 7 — Tell the user the next steps (never commit)

```
✓ Engine:   <installed | present>  model <warmed | not warmed>
✓ Wiring:   .mcp.json + .claude/settings.json (portable) [+conflicts resolved]
✓ Rule:     .claude/rules/mnemo-memory.md (binding, all agents)
✓ CLAUDE.md: team-lead section
✓ Agents:   planner/developer/tester/reviewer — memory: project
✓ Team:     CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 (if approved)
✓ Memory:   one-line anchor [+ migrated N files]

Review the diffs above, then:
  git add .mcp.json .claude/ CLAUDE.md
  git commit        # your message, your call
Then trust the project in Claude Code when prompted (hooks + MCP) and
restart the session so hooks, MCP and the agent team load.
```

Do not run `git add`/`git commit` yourself.

## Edge cases

- **Engine absent and mnemo repo unavailable**: explain the two-layer
  model, stop before wiring (wiring without an engine is inert).
- **Already fully wired (portable) + rule present**: `mnemo init` is a
  no-op; go straight to CLAUDE.md / agents / team-flag / migration.
- **Agent deliberately non-project memory**: still required to be
  `project` here — explain why (shared team memory); apply with the
  diff. The requirement is firm; only the content is shown for review.
- **Foreign MCP servers / hooks / settings keys**: `mnemo init` and the
  team-flag merge are additive — never reorder or rewrite foreign
  entries.
- **No `CLAUDE.md` and the user resists**: the team-lead section is
  mandatory; without it the main session has no standing instruction to
  act as lead or to use the memory. Create it (with the diff shown).
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
