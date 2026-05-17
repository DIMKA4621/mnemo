---
name: mnemo-adopt
description: >
  Adopt mnemo (curated-markdown project memory + local rebuildable
  vector index) into a project. Orchestrates the deterministic
  primitives — `install.sh` (user-scope engine) and `mnemo init`
  (git-tracked, additive, portable project wiring) — and handles every
  judgement call with a shown diff and AskUserQuestion: weaving the
  memory section into an existing CLAUDE.md, inspecting and wiring
  subagents, resolving `mnemo init` conflicts, and migrating a
  project's user-scope built-in Claude memory (project and per-agent)
  into the git-tracked project memory. Never edits a human-authored
  file blind. Never commits.

  Use when the user asks to: adopt/set up/init/bootstrap mnemo in a
  project, add shared searchable memory to a project, wire mnemo hooks
  and MCP, migrate built-in Claude memory into the project, give
  subagents project memory. Triggers on: "adopt mnemo", "set up mnemo",
  "init mnemo", "mnemo в проект", "підключи mnemo", "додай памʼять
  проєкту", "перенеси памʼять у проект", "bootstrap project memory".
---

# mnemo adopt

Bring a project under **mnemo**: curated markdown in git is the single
source of truth; a local, disposable, rebuildable vector index makes it
searchable. This skill is the guided operator on top of two
deterministic primitives — it inspects, asks, runs the primitives, then
resolves everything they deliberately refuse to touch.

## The hard boundary (do not cross it)

- **Deterministic primitives do the safe, mechanical work.**
  `install.sh` installs/reports the engine; `mnemo init` creates only
  absent files and merges `.mcp.json` / `.claude/settings.json` strictly
  additively, refusing on conflict. They never touch `CLAUDE.md`, never
  overwrite curated memory, never resolve a conflict.
- **This skill + you do every judgement call** — and only with a shown
  diff and explicit confirmation: integrating the memory section into a
  human-authored `CLAUDE.md`, resolving an `mnemo init` refusal,
  enabling subagent memory, curating migrated memory. Never improvise a
  file the primitive should have produced. **Never commit** — the user
  reviews and commits.

## Mental model

Two layers (see `references/mnemo.md`):

- **Engine** — user scope, once per machine: `~/.claude/mnemo/`
  (venv + embedding model cache + index state + launcher). Not in git.
- **Wiring** — git-tracked, per project: `.mcp.json`,
  `.claude/settings.json` hooks, `.claude/memory/` skeleton,
  `.claude/agent-memory/`. Ships to everyone who clones.

The git-tracked invocation is portable by construction (`mnemo init`
writes a shell-wrapped `$HOME` form — no machine path lands in git).
`mnemo` is never a human command; only hooks, the MCP registration and
this skill call it.

## Workflow

### Step 1 — Inspect

Establish engine state and project state. Do not change anything yet.

**Engine** (user scope):

```bash
ls -la ~/.claude/mnemo/bin/mnemo ~/.claude/mnemo/.venv 2>/dev/null
ls -A ~/.claude/mnemo/model-cache 2>/dev/null | head -1
```

If the mnemo repo is available, prefer the precise report:
`bash <mnemo-repo>/install.sh --check`. Conclude: engine **installed?**
model **warmed?**

**Project** (current directory = project root):

```bash
ls -la .mcp.json CLAUDE.md .claude 2>/dev/null
ls -la .claude/memory .claude/agent-memory .claude/agents \
       .claude/settings.json 2>/dev/null
```

Read each found file. Determine:

- Is mnemo already wired (an `mnemo` server in `.mcp.json`; the three
  hooks in `.claude/settings.json`)? In the **portable** form or an old
  hardcoded path (→ a conflict `mnemo init` will refuse → Step 4)?
- Does `.claude/memory/` already hold curated memory, or is it
  empty/absent?
- **Subagents**: for each `.claude/agents/*.md`, read the frontmatter
  `memory:` field — is agent memory enabled, and at what scope? List
  agents with memory **off** or **not project-scoped**.
- **User-scope built-in memory for THIS project**: compute the slug and
  inspect it per `references/memory-migration.md`. Note whether
  project-level and/or per-agent built-in memory exists there.

Build a findings block:

```
ENGINE:    installed=<y/n>  model-warmed=<y/n>
WIRING:    .mcp.json=<absent|portable|HARDCODED|foreign+mnemo?>
           settings hooks=<absent|portable|HARDCODED>
MEMORY:    project .claude/memory=<absent|empty|curated:N files>
           user-scope built-in (<slug>)=<absent|present:N files>
AGENTS:    <role> memory=<off|user|project>  (one line each)
           user-scope agent memory=<none|present: roles…>
```

### Step 2 — Ask the user what to do

**MANDATORY — never skip, never assume, never default.** Use
`AskUserQuestion`. Ask only the questions that the findings make
relevant; phrase each from the findings, not generically.

Decisions to surface (skip any that does not apply):

1. **Engine missing or model not warmed** — run `install.sh` now? run
   `mnemo warmup` now (one-time ~2.2 GB, explicit)? yes/no each.
2. **Wiring conflict found** (old hardcoded mnemo entry) — migrate it
   to the portable form? (You will show the exact diff in Step 4.)
3. **User-scope built-in project memory found** while project memory is
   absent/thin — migrate it in? Present the **file list**; selection
   default = **none**; never pre-select; the content will be curated
   and shown before writing.
4. **Subagents with memory off / non-project** — enable project memory
   for them? Present the list by name (`multiSelect`), default none.
5. **User-scope per-agent memory found** — pull it into
   `.claude/agent-memory/<role>/`? List by role, default none.
6. **No subagents at all** — offer starter agents (e.g. developer,
   tester, reviewer) from `templates/agent.md.template`? yes/no/which.
7. **CLAUDE.md present** — weave in the mnemo memory section? (Diff
   shown in Step 4. If absent, one will be created.)

**If `AskUserQuestion` is unavailable** (non-interactive): stop, print
the findings block and the decisions, ask the user to re-run
interactively. Do NOT proceed on assumptions.

### Step 3 — Run the deterministic primitives

In order, only what the user approved:

```bash
bash <mnemo-repo>/install.sh            # if engine missing
~/.claude/mnemo/bin/mnemo warmup         # if approved (one-time)
~/.claude/mnemo/bin/mnemo init --root "$PWD"
```

Capture `mnemo init` output verbatim. If it printed
`mnemo init: refused — …` it wrote **nothing**: this is expected when an
old hardcoded entry exists — carry the found/expected detail into Step 4.
Re-run `mnemo init` after the conflict is resolved so the rest of the
additive wiring is applied.

### Step 4 — Judgement (always a shown diff, never blind)

For each item, render the precise before/after, get explicit
confirmation, then apply. Apply nothing the user did not confirm.

- **CLAUDE.md memory section.** Use `templates/CLAUDE.section.md`. If no
  `CLAUDE.md`, create it with that section. If one exists: find a
  natural insertion point (a new top-level `## Project memory (mnemo)`
  section; if a memory/agent-instruction section already exists,
  reconcile rather than duplicate). Show the diff. Subagents do **not**
  inherit `CLAUDE.md` — agent-facing memory rules go in each agent file,
  not only here.
- **Wiring conflict resolution.** For the entry `mnemo init` refused,
  show `found:` vs the portable `expected:` and apply the replacement
  in `.mcp.json` / `.claude/settings.json`. The portable form is the
  one `mnemo init` emits — copy it from the refusal report, do not
  hand-author a variant. Re-run `mnemo init` afterwards.
- **Subagent memory.** For each chosen agent, show the frontmatter edit
  (`memory: project`) as a diff; create
  `.claude/agent-memory/<role>/MEMORY.md` from
  `templates/agent-memory.md.template`. For new starter agents, use
  `templates/agent.md.template` (keep its memory section intact).
- **Memory migration.** Per `references/memory-migration.md`: copy the
  chosen user-scope files into `.claude/memory/` (and per-agent into
  `.claude/agent-memory/<role>/`). Curate while copying — keep
  `MEMORY.md` a thin index, move detail into topic files, day notes
  under `logs/`. Show what goes where (and any content that looks like
  noise/session-state, which you should drop) before writing. The
  source is the user's accumulated built-in memory: treat it as a
  bootstrap seed, not gospel.

### Step 5 — Verify

```bash
~/.claude/mnemo/bin/mnemo ingest --root "$PWD"      # build/refresh index
python3 -m json.tool .mcp.json > /dev/null && echo "mcp JSON OK"
python3 -m json.tool .claude/settings.json > /dev/null && echo "settings JSON OK"
~/.claude/mnemo/bin/mnemo search "architecture" --root "$PWD" | head
```

Confirm: `mnemo` server present in portable form; the three hooks
present; index built; a search returns curated content. If `mnemo
ingest` reports the model is not warmed, that is expected when warmup
was declined — say so plainly.

### Step 6 — Tell the user the next steps (never commit)

Print a tight summary:

```
✓ Engine:   <installed | already present>  model <warmed | not warmed>
✓ Wiring:   .mcp.json + .claude/settings.json (portable) [+ conflicts resolved]
✓ Memory:   .claude/memory/ skeleton [+ migrated N files]
✓ Agents:   <role>… memory=project [+ migrated]
✓ CLAUDE.md: mnemo section woven in

Review the diffs above, then:
  git add .mcp.json .claude/ CLAUDE.md
  git commit        # your message, your call
Then: trust the project in Claude Code when prompted (hooks + MCP),
and restart the session so the hooks and the mnemo MCP server load.
```

Do **not** run `git add`/`git commit` yourself. State exactly what
changed so the user reviews and commits.

## Edge cases

- **Engine absent and mnemo repo not available**: explain the two-layer
  model, point to where the engine must be installed (`~/.claude/mnemo/`
  via `install.sh`), stop before wiring — wiring without an engine is
  inert.
- **`mnemo init` refuses on `.mcp.json` AND a hook**: it wrote nothing
  for either; resolve both conflicts in Step 4, then a single re-run
  applies all additive parts.
- **Project already fully wired (portable)**: `mnemo init` is a no-op
  (idempotent) — skip to memory/agent/CLAUDE.md judgement only.
- **`.claude/memory/` already curated**: never overwrite it; migration
  only adds files that are absent, and only with confirmation.
- **Foreign MCP servers / hooks present**: `mnemo init` preserves them
  untouched (additive). Do not reorder or rewrite them in Step 4 either.
- **Subagent uses `memory: user` or a custom scope deliberately**: do
  not flip it silently — surface it and let the user decide; the
  default recommendation is `project` so the team shares it.
- **No `CLAUDE.md` and the user declines creating one**: still wire and
  migrate; warn that without the memory section the main session has no
  standing instruction to use/maintain the memory (agents still do via
  their own files).
- **Slug ambiguity / no user-scope memory found**: state it plainly;
  proceed with the empty skeleton `mnemo init` created.

## Reference files

- `references/mnemo.md` — how mnemo works: the two layers, the data
  flow (`.md → chunks → embeddings → sqlite-vec+FTS5 → search`), the
  portable invocation form, why the index is disposable.
- `references/memory-migration.md` — the user-scope slug formula and
  exact paths (project + per-agent built-in memory), how to read a
  subagent's `memory:` frontmatter, and the curation rules when copying
  migrated memory in.
- `templates/CLAUDE.section.md` — the affirmative mnemo section to weave
  into a project `CLAUDE.md`.
- `templates/agent.md.template` — a subagent definition that uses mnemo
  (memory section must stay intact; subagents don't inherit `CLAUDE.md`).
- `templates/agent-memory.md.template` — a per-agent memory block.
- `templates/MEMORY.md.template` — the thin index shape, used as the
  target when curating migrated memory.
