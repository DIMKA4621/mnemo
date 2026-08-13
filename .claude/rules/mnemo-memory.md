# Project memory (mnemo) — binding rule

Everything below the divider is **portable**: it is the whole instruction for
working with this project's memory, and it stands on its own. If an agent or a
platform has no notion of rule files, paste that part into its system prompt,
give it the `mnemo-memory` MCP server, and it has what it needs.

**This part is Claude Code specific.** The file lives at
`.claude/rules/mnemo-memory.md` and auto-loads for everyone in the session —
the team lead **and every subagent** (subagents do not inherit `CLAUDE.md`, so
this is the one place the discipline binds for all). It replaces any default or
built-in memory behavior.

---

## Where memory lives

The project's **own** `.claude/memory/` at the repository root — not
`~/.claude/`, not any user-level or session-local store. One root, everything
nested inside it:

```
.claude/memory/
  MEMORY.md          index: links + quick facts, kept under ~200 lines
  logs/YYYY-MM-DD.md what was done that day, decisions, commits
  topics/<name>.md   one concept per file: architecture, research, pitfalls
  agents/<role>/     per-role memory, when a project has agent roles
```

Everything else under `.claude/` — `agents/`, `rules/`, `skills/`,
`settings.json` — is **not** memory and is not part of the searchable bank.
That is why memory nests under one root: the boundary is the folder, so no
exclusion list has to be maintained.

The curated markdown is the **single source of truth**. The vector index is
derived from it, disposable, and rebuilt automatically — never edit the index,
and never treat it as a place where something is stored.

## Searching — first, before anything else

**The order is: search, read, then answer.** Never answer first and check
afterwards. A reply composed before the search is one the search cannot
repair — the best you can do then is paste a correction underneath it, and
the user has already read the wrong thing.

**You have not consulted memory until you have called `search` in this
session, for this task.** Text that happens to be in your context is not a
search result: it may be stale, it may be about something else, and it is not
evidence that anything was checked. Do not reason from "I think I already have
this".

### How to search — the protocol

**One query is not a search.** The index matches phrasing, not meaning in
general, so a single formulation that comes back empty proves nothing about
what is recorded.

1. **Ask two or three different questions**, not one question twice. Vary the
   angle: the symptom and the cause are separate queries; the name of a thing
   and what it does are separate queries; a decision and its rationale may sit
   in different files. Both languages hit — the bank holds whatever was
   written.
2. **Widen first, narrow second.** Leave `path_prefix` out to sweep the whole
   bank; add it (`logs`, `topics`, `agents/reviewer`) once you know roughly
   where the answer lives. `tree` shows the layout with each file's headings.
3. **A chunk is a pointer, not the answer.** It is a fragment cut to fit an
   index. Open the file it came from and read around it — the qualification
   that reverses the meaning is often in the next paragraph.
4. **Follow the links.** `MEMORY.md` indexes `topics/`; a topic file names the
   log that produced it. Two hops is normal, not thoroughness.
5. **Stop when fresh queries stop surfacing fresh files** — that, and not the
   first plausible hit, is what "I checked" means.

Assemble the whole picture — the log, the topic, the research note — **before**
planning or answering. Half a record is how a settled decision gets
relitigated.

### Searching is the default, not a trigger you look for

Every user message that asks, decides or changes something **begins** with a
search. Not a subset of them:

- any question at all — including one that looks like general knowledge;
- planning, or proposing an approach;
- changing architecture, an interface, or a schema;
- debugging anything that is not a one-line typo;
- "why is this like this?", "did we try X?", "what did we decide about Y?";
- anything that smells like it was settled before.

**Do not answer out of your own knowledge until you have looked.** Your
training does not contain this project. What you recall from earlier in this
conversation is not evidence either — it may be stale, it may describe a
different part of the system, and nothing checked it against the record.
Feeling certain is not the same as having looked.

The only messages that need no search are the ones with no question and no
decision in them: "run the tests", "commit that", "yes". The moment such a
message turns into a judgement call, search before making it.

The asymmetry settles it. A search that finds nothing costs you a second. A
skipped search costs the project a contradiction — and the user has to be the
one who notices.

### Say that you searched

State what you looked for and what came back, in a line. Not ceremony: it is
what lets the person reading tell an answer grounded in the record from one
that merely sounds confident — and it is the difference they cannot check any
other way.

Read what comes back. A recorded decision is not a suggestion — if you intend
to go against one, say so explicitly and say why.

Three answers mean three different things, and they are not interchangeable:

| Answer | Meaning | What to do |
|---|---|---|
| `status=ready`, no hits | genuinely nothing recorded | proceed, then record what you learn |
| `status=indexing` | the index is still building | retry shortly — do **not** conclude "no memory" |
| `status=empty` | nothing indexed yet at all | say so; the bank may need registering |

## Writing — after significant work or any decision

| What | Where |
|---|---|
| Key paths, architecture overview, quick facts | `MEMORY.md` |
| What was done today, decisions, the commit's subject | `logs/YYYY-MM-DD.md` |
| Architecture deep-dives, one concept per file | `topics/<name>.md` |
| Research, benchmarks, debugging conclusions | `topics/<topic>-research.md` |
| Per-role notes, when the project has agent roles | `agents/<role>/` |

- `MEMORY.md` stays an **index**: links and quick facts, under ~200 lines. When
  it outgrows that, move detail into `topics/` and leave a link.
- Write more rather than less: a redundant entry costs nothing, a lost insight
  costs the next session's time. When in doubt, record it.
- Record **research and debugging conclusions**, not just outcomes — the dead
  ends are what save the next attempt.
- No duplicates: check what is recorded before adding.
- No session state ("currently doing X") — only durable knowledge.
- Remove entries that became wrong. Stale memory is worse than none.
- Do not record what the code, the git history or `CLAUDE.md` already says.

## Hard constraints

- Edit only the `.md` under `.claude/memory/`. Use native file tools; there is
  no memory-write tool and there will not be one.
- Never write shared knowledge to `~/.claude/` or any user-level,
  session-local or built-in memory. Only the project's git-tracked
  `.claude/memory/` counts.
- Reindexing is automatic: a background service watches these files and
  re-indexes within seconds of a save. You never run a command for it.
  `reindex` exists only to force the issue.
- **Memory rides with the commit.** When a memory `.md` change accompanies a
  code change, `git add` both and land them in the **same** commit. Refer to
  that commit by its **subject/scope, never by a hash** — hashes break on
  force-push, rebase and amend. Never leave memory uncommitted behind a code
  commit.
