# Migrating built-in Claude memory into the project (reference)

When a project has no (or only thin) mnemo memory, Claude may already
have accumulated **user-scope built-in memory** for that exact project.
That is a valuable bootstrap seed — offer to migrate it into the
git-tracked project memory. This is a judgement step: detect
deterministically, then curate and copy only with confirmation.

## Locating the user-scope memory (slug)

Built-in memory for a project lives under:

```
~/.claude/projects/<slug>/memory/
```

`<slug>` is derived from the project's absolute path by replacing the
path separators and underscores with `-`. Because the path is absolute
it begins with `-`. Example (empirically confirmed):

```
/home/user/projects/mnemo
→ -home-user-projects-mnemo
```

So: `slug = project_abs_path` with `/` → `-` and `_` → `-`.

**Do not trust the formula blindly — confirm by listing.** Compute the
candidate, then:

```bash
ls -1 ~/.claude/projects/ | grep -F "$(basename "$PWD")"
ls -la ~/.claude/projects/<slug>/ 2>/dev/null
```

Match the directory whose name corresponds to the current project's
absolute path. If none matches, there is no built-in memory to migrate
— say so and proceed with the empty skeleton.

## What to inspect there

- `~/.claude/projects/<slug>/memory/MEMORY.md` — the built-in index.
- `~/.claude/projects/<slug>/memory/*.md` and any `logs/` — topic and
  day files.
- Anything that is clearly **session state** ("currently working on
  X"), one-off, or already obsolete — do **not** migrate it. Migrate
  durable knowledge only.

## Per-agent memory

Subagents declare memory in their definition frontmatter
(`.claude/agents/<role>.md`):

```yaml
memory: project   # shared in git under .claude/agent-memory/<role>/
```

- `memory: project` → already git-shared; nothing to migrate, just
  confirm `.claude/agent-memory/<role>/` exists.
- `memory: user` (or a non-project scope) → that agent's notes are kept
  at user scope. If such notes exist for this project's agents under
  the same `~/.claude/projects/<slug>/` tree, offer to pull them into
  `.claude/agent-memory/<role>/` and flip the agent to `memory: project`
  (shown as a diff). Never flip scope silently — the choice may be
  deliberate.
- No `memory:` field → agent memory is effectively off; offer to enable
  `memory: project`.

Discover the exact on-disk location of any per-agent built-in notes by
listing the project slug directory rather than assuming a fixed
subpath; confirm what you found with the user before copying.

## Curation rules when copying in

The target is mnemo's native shape — do not dump files verbatim:

- `MEMORY.md` stays a **thin index**: quick facts + links. Move any
  detail the built-in `MEMORY.md` inlined into proper topic files.
- One concept per topic file (`architecture.md`, `database.md`, …).
- Day-by-day notes → `logs/YYYY-MM-DD.md` (append-only).
- Agent-specific knowledge → `.claude/agent-memory/<role>/`, not the
  shared index.
- Drop duplicates, session state, and anything already captured in the
  codebase or `CLAUDE.md`.
- Show the planned mapping (source file → target file, what is dropped
  and why) before writing anything. The built-in memory is a seed to
  curate, not a transcript to preserve.

## Not a migration target

`.claude/rules/mnemo-memory.md` and the one-line `.claude/memory/
MEMORY.md` anchor are written by `mnemo init`, not by migration. Never
copy built-in memory into the rule file, and do not preserve a fat
built-in `MEMORY.md` over the thin anchor — curate its content into
topic files instead and keep `MEMORY.md` a thin index.

## After migration

Let `mnemo ingest` rebuild the index from the new `.md` (the Verify
step). The user-scope built-in memory is left in place — migration
copies, it does not move or delete. The git-tracked `.claude/memory/`
is now the source of truth going forward.
