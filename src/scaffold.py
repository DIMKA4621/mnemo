"""`mnemo init` — deterministic, idempotent project wiring.

This is a SAFE primitive, not a judgement call. It only ever:
  * creates, only when ABSENT, the bare minimum: a one-line
    `.claude/memory/MEMORY.md` anchor and the binding memory rule
    `.claude/rules/mnemo-memory.md` (never invents memory structure,
    never overwrites a curated or human-authored file);
  * refreshes `.claude/rules/mnemo-memory.md` in place when its bytes hash
    to a redaction mnemo itself wrote earlier — that text is mnemo's own and
    has grown, and a project adopted last month would otherwise keep last
    month's rules forever. Any digest that is not one of ours means a person
    edited the file, and it is then left exactly as it is;
  * merges strictly ADDITIVELY into the project's MCP wiring and
    `.claude/settings.json` (adds only mnemo's own keys/hook groups, never
    touches or reorders foreign content);
  * refuses — writing NOTHING — if a *different* mnemo entry already
    exists, leaving that migration to the adopt skill (shown diff +
    confirmation). It never edits CLAUDE.md.

**Where the entry goes depends on the project, and getting it wrong is
silent.** A project set up by the user-scope `project-mcp-setup` skill keeps
`.mcp.json` **generated and git-ignored**, regenerated wholesale from
`.mcp.json.template` by `mcp-setup.sh` — so anything written straight into
`.mcp.json` there is erased on the next run, with no error. `init` therefore
looks for a template first: finding one it writes the placeholder entry into
the template, the variables into `.mcp.env.example`, the real values into
`.mcp.env`, and the substitutions into `mcp-setup.sh`. Finding none it writes
`.mcp.json` directly and makes sure git ignores it.

**The credential written is the bank's own token, and it is the whole
address.** It opens that one bank's two read tools, and it is what tells the
backend which bank this connection is for — there is no bank name and no path
segment in anything `init` writes. The service-wide token belongs to the
cabinet, the CLI and the admin face, and never to a project file.

**No hook is written, and no flag makes one appear.** `settings.json` is
left alone entirely; `--migrate` only *removes* hooks earlier generations
wrote. The discipline lives in `.claude/rules/mnemo-memory.md` and nowhere
else, because two mechanisms stating the same rule are two that can drift.

The git-tracked layer stays portable by construction: both varying values in
the MCP entry — port and token — are `{{VAR}}` in the template, resolved from
a git-ignored `.mcp.env`. No machine-specific path and no secret is written into
a git-tracked file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterable

from . import config
from .config import resolve

# Portable launcher reference. Each teammate's own $HOME resolves at run
# time — nothing machine-specific lands in git. Needed only by the hook seeds;
# the MCP wiring no longer uses it at all.
_LAUNCHER = "~/.claude/mnemo/bin/mnemo"


# The `mcpServers` key `init` writes, and the one thing in the file that says
# which bank this entry is for -- a person reads the key, not a URL full of
# hex. `-memory` is in it because a project may well grow a second entry
# (`mnemo-notes`, `mnemo-specs`) pointed at another bank, and a first entry
# called plainly `mnemo` would then read as "the mnemo one" among siblings
# that are equally mnemo. Tools namespace from it: `mcp__mnemo-memory__search`.
_INSTANCE = "mnemo-memory"

# What that key used to be. Kept so `--migrate` can rename it rather than
# leaving a project with two entries authenticating into the same bank, which
# is what a plain re-`init` would otherwise produce.
_LEGACY_INSTANCE = "mnemo"

# Variable names are never derived from the *bank* name: bank names are human
# labels that hold spaces and Cyrillic, which no shell variable name may
# contain. Keeping the bank on the value side means the instance name — ASCII,
# ours — is the only thing that has to be shell-safe.
#
# The env-var prefix for the default entry stays `MNEMO_`, deliberately out of
# step with `_INSTANCE`. The key is what a human reads; the variables are the
# template convention's private plumbing, and renaming them to
# `MNEMO_MEMORY_*` would rewrite `.mcp.env`, `.mcp.env.example` and every
# `sed -e` line in `mcp-setup.sh` across every adopted project -- for no
# readability gain, and straight into the one failure that is silent: a
# placeholder with no matching `-e` line passes through verbatim while the
# script still exits 0. A *second* instance still derives its own prefix from
# its own name, which is what `_var_prefix` is for.
_VAR_INSTANCE = "mnemo"


def _var_prefix(instance: str) -> str:
    """`mnemo` -> `MNEMO`; `mnemo-notes` -> `MNEMO_NOTES`."""
    return re.sub(r"[^A-Za-z0-9]+", "_", instance).strip("_").upper() or "MNEMO"


def _api_port() -> str:
    return str(
        getattr(config, "API_PORT", None)
        or os.environ.get("MNEMO_API_PORT", "8918")
    )


def _mcp_server(token: str) -> dict:
    """The v3 MCP entry (§10.4), written straight into a `.mcp.json`.

    **The token is the whole address.** It belongs to one bank, so the bank
    needs no separate mention — and must not get one: two things that say
    which bank can disagree, and the URL would be the one that is wrong.
    That is why there is no `bank_name` parameter here any more.

    The token is this bank's own, literal, which is why this form is only ever
    written into a `.mcp.json` that git ignores. It is not the service token:
    it opens this one bank's two read tools and nothing else, so a project
    file never carries the credential that reaches every bank on the machine.
    A project that keeps a `.mcp.json.template` gets the placeholder form
    instead — see `_mcp_server_template`.

    No `headers` key. The value rides in the URL because a header depends on
    Claude Code forwarding `headers` for `type: http`, and if it does not,
    authentication fails outright. `X-Mnemo-Bank` is gone for a second reason
    on top of that: under token addressing it could only ever contradict the
    credential.

    **What tells a reader which bank this is** is the `mcpServers` key —
    `mnemo`, `mnemo-notes` — which is what a person actually reads in a
    config. A cosmetic path segment that routing ignored would be worse than
    none: the next person reads a path component as routing.
    """
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{_api_port()}/mcp?token={token}",
    }


def _mcp_server_template(instance: str) -> dict:
    """The same entry with `{{VAR}}` placeholders, for a `.mcp.json.template`.

    Shape pinned by the user-scope `project-mcp-setup` skill
    (`templates/mnemo.example`) — one URL string with a placeholder at each
    varying position, never a URL split across files. Two placeholders now,
    not three: `{{MNEMO_BANK}}` went with the path segment.
    """
    prefix = _var_prefix(instance)
    var = lambda name: "{{" + f"{prefix}_{name}" + "}}"  # noqa: E731
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{var('PORT')}/mcp?token={var('TOKEN')}",
    }


def _is_mnemo_http(entry: object) -> bool:
    """Mnemo's own HTTP entry — ours to rewrite in place, current or not.

    Matches the literal and the placeholder form, and **also the superseded
    `/mcp/<bank>?token=…` shape**, which is the third generation `--migrate`
    has to fix (§11.3). All three are entries mnemo authored; which one it is
    decides whether the plan rewrites or leaves alone, and that comparison is
    `existing == target`, made by the caller. Anything else under the `mnemo`
    key is either a recognised stdio legacy or a refusal.
    """
    return (
        isinstance(entry, dict)
        and entry.get("type") == "http"
        and isinstance(entry.get("url"), str)
        and "/mcp" in entry["url"]
    )


# The two legacy generations `--migrate` must recognise (§15.2). L1 predates
# the windows-native branch; L2 is what that branch shipped and is therefore
# what most recently-adopted projects carry. `init` refuses BOTH; only
# `--migrate` rewrites them, and only because mnemo authored them itself.
def _is_legacy_mcp(entry: object) -> str | None:
    """Return 'L1' / 'L2' for a mnemo-authored stdio entry, else None."""
    if not isinstance(entry, dict):
        return None
    command = entry.get("command")
    args = entry.get("args")
    if not isinstance(command, str):
        return None
    # L1: {"command": "/bin/sh", "args": ["-c", "exec \"$HOME/…/mnemo\" mcp"]}
    if command.endswith("sh") and isinstance(args, list):
        joined = " ".join(a for a in args if isinstance(a, str))
        if "mnemo" in joined and "mcp" in joined.split():
            return "L1"
    # L2: {"type": "stdio", "command": "${HOME}/.claude/mnemo/bin/mnemo",
    #      "args": ["mcp"]}
    if "mnemo" in command and isinstance(args, list) and args[:1] == ["mcp"]:
        return "L2"
    return None

# `init` writes NO hook. Not "none by default" — there is no longer a flag
# that makes it write one, and the two seeds it used to offer are gone
# (design #15, #27).
#
# Both were injections, and injection is the wrong shape for this. Memory has
# to be *fetched under a question* to mean anything: `hook-inject` handed over
# top-N sections before the task was even stated, which reads as memory
# already gathered, and `memory-hook` handed over the index — useful, but the
# `tree` tool answers the same question on demand, and a map that arrives
# unasked competes with the rule that says go and look.
#
# So the discipline lives in exactly one place, `.claude/rules/mnemo-memory.md`,
# and nothing else states it. Two mechanisms saying the same thing is two
# mechanisms that can disagree, and the one nobody edits wins by accident.
#
# Hooks earlier generations wrote, which `--migrate` removes. Pairs, not a
# dict keyed by event: `SessionStart` appears twice with different
# subcommands, and a dict would silently lose one.
#
#   * v2: `SessionStart -> ingest`, `PostToolUse -> hook-postedit` — both did
#     work the watcher now does, and did it *inside* the session, which is the
#     blocking behaviour v3 exists to remove.
#   * early v3: `UserPromptSubmit -> hook-inject`, wired automatically; then
#     opt-in; now gone entirely.
#   * mid v3: `SessionStart -> memory-hook`, the opt-in map seed. Also gone.
_RETIRED_HOOKS: tuple[tuple[str, str], ...] = (
    ("SessionStart", "ingest"),
    ("PostToolUse", "hook-postedit"),
    ("UserPromptSubmit", "hook-inject"),
    ("SessionStart", "memory-hook"),
)

# The strict, universal project-memory rule. `.claude/rules/*.md`
# auto-loads into the team lead AND every subagent (subagents do not
# inherit CLAUDE.md), so this is the one place the discipline binds for
# all. This text is the single source — the adopt skill references it
# for conflict resolution rather than duplicating it.
_MEMORY_RULE = """\
# Project memory (mnemo) — binding rule

Everything below the divider is **portable**: it is the whole instruction for
working with this project's memory, and it stands on its own. If an agent or a
platform has no notion of rule files, paste that part into its system prompt,
give it the `mnemo` MCP server, and it has what it needs.

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

The MCP tool is **`search`**. Narrow with `path_prefix` when you know
roughly where to look (`logs`, `topics`, `agents/reviewer`); leave it out to
search the whole bank. `tree` shows the layout with each file's
headings.

**You have not consulted memory until you have called `search` in this
session, for this task.** Text that happens to be in your context is not a
search result: it may be stale, it may be about something else, and it is not
evidence that anything was checked. Do not reason from "I think I already have
this".

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

- `MEMORY.md` stays an **index**: links and quick facts, under ~200 lines. When
  it outgrows that, move detail into `topics/` and leave a link.
- One concept per file in `topics/`. Day-by-day notes in `logs/`.
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
"""


# Every redaction of `_MEMORY_RULE` this program has ever written, by digest.
#
# The rule is mnemo's own text, and it has grown: the "search is the default,
# not a trigger" section did not exist when the first projects were adopted.
# Seeding it only when absent — what this used to do — meant a project got
# whichever wording happened to be current on the day it was adopted and then
# kept it forever, with nothing anywhere saying it was out of date.
#
# So `init` may replace the file, but only when it can prove the bytes are
# still exactly one of ours. An unrecognised digest means a person edited it,
# and their edit outranks the update.
#
# Two digests per redaction because the writer changed. Up to `_write`, the
# file went out through `Path.write_text` with no `newline=`, so a project
# adopted on Windows has CRLF where a Linux one has LF — same text, different
# bytes. The current redaction's CRLF form is added at the bottom for the same
# reason: it is ours, it is stale only in its line endings, and rewriting it
# normalises the file.
#
# To regenerate after changing the rule text: hash the OLD constant (LF and
# CRLF) and add both lines here. `git show <commit>:src/scaffold.py` parsed
# with `ast` gets any past redaction back — the constant has never been built
# by formatting, so what is in the source is exactly what reached disk.
_RULE_SUPERSEDED: tuple[str, ...] = (
    # v1 — 8312de3 feat: team-lead adoption model
    "3047e5574a62de5483bd906f0e44f667f14671683e0c67ab6b3edd363b891f13",
    "e1337da2f293fd442ad4d3d8af57f32060868ef092018343899d16c6f29b994e",
    # v2 — 5d5fab3 docs: memory rule rides with the commit
    "0403054c873ec08d01fa3a89990f6d544b936db146dab1403781c035dc427937",
    "11a5d4937e01ca5722205ca37ae3f87e489785e4cf807520ee7bad962e14db2f",
    # v3 — 4b80845 feat(service): phase 4, faces become thin clients
    "36bee6bcfde19122d96bd211ae3b1e702000a791736dc7dade4174ffbbd63769",
    "6b5ff2bdf2dc318a0d7f7cbb978860f0f87dc5c6783f2ee82f5e38ad1fefae73",
    # v4 — 307c5bf feat!: make MCP the primary face
    "a175f81f7f29c7f3862d824fb0507051d6d2e3b0909978afd7cbc14c816f9261",
    "e8ecd514ce782bc7ba8b6c79d3ca5b91a674fe98e1a11cd7e097208eda714e38",
    # v5 — 895208b refactor(mcp)!: drop the memory_ prefix
    "83fe733cb9ccddb197c3643d47a7235c077701c6485da211d4950b7597726a8a",
    "21439e14abc870183ed5477c40da74cf1c5b91d13058455a4270fdb18217760e",
)
# v6 — 42351ff feat!: delete the hook seeds — is the current text, so both of
# its digests are computed from the constant rather than listed. A literal
# would be free to fall out of step with the text it claims to describe.


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_RULE_CURRENT = _digest(_MEMORY_RULE)
_RULE_ANCESTORS: frozenset[str] = frozenset(
    _RULE_SUPERSEDED + (_digest(_MEMORY_RULE.replace("\n", "\r\n")),)
)


def _rule_state(path: Path) -> str:
    """Classify an existing `mnemo-memory.md`: current, stale or edited.

    Read as bytes and hashed as bytes: the question is what is literally on
    disk, so no decoding and no newline translation may sit in between.
    """
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "edited"          # unreadable is not ours to overwrite
    if digest == _RULE_CURRENT:
        return "current"
    if digest in _RULE_ANCESTORS:
        return "stale"
    return "edited"


class _Refuse(Exception):
    """A different mnemo entry already exists — migration is a judgement
    call for the adopt skill, not for this primitive."""


def _load_json(path: Path) -> dict:
    """Parse an existing JSON object. A present-but-broken or non-object
    file is a refusal, never something we silently clobber."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        raise _Refuse(f"{path} exists but is not readable JSON ({exc}); "
                      f"left untouched") from exc
    if not isinstance(data, dict):
        raise _Refuse(f"{path} is not a JSON object; left untouched")
    return data


def _dump_json(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _redacted(entry: dict) -> str:
    """An entry as it may appear in a message on somebody's terminal.

    A refusal prints what `init` *would* have written so the reader can see
    the difference — and the literal form carries a live bank token. Refusals
    are copied into issues and pasted into chats; the token is not what the
    reader needs, so it never reaches the screen.
    """
    return re.sub(r"token=[0-9a-fA-F]{8,}", "token=<bank-token>",
                  json.dumps(entry))


def _plan_servers(path: Path, label: str, log: list[str], target: dict,
                  *, migrate: bool) -> str | None:
    """Merge mnemo's entry into an `.mcp.json`-shaped document.

    Shared by the plain file and the template, because the two differ only in
    what `target` looks like — the conflict rules, the additive merge and the
    refusal wording are identical, and writing them twice is how they drift.

    The classification runs **before** `target` matters, which is what lets
    `init_project` validate (and refuse) before it has registered a bank or
    fetched a token: every refusal below is a property of what is already in
    the file.
    """
    data = _load_json(path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    elif not isinstance(servers, dict):
        raise _Refuse(f"{path}: 'mcpServers' is not an object; left untouched")

    # The key was renamed (`mnemo` -> `mnemo-memory`). A project wired before
    # that carries the old one, and simply writing the new key beside it would
    # leave two entries authenticating into the same bank -- two connections,
    # duplicate tools, and no hint which is which. So the old key is *renamed*
    # under `--migrate`, and a plain run refuses and says so.
    #
    # Only ever a key mnemo itself authored: a server somebody else called
    # `mnemo` is left exactly where it is, like any foreign key.
    renamed = False
    legacy_key = servers.get(_LEGACY_INSTANCE)
    if legacy_key is not None and _is_mnemo_http(legacy_key):
        # Ours, in HTTP form, under the former key. Renamed by a plain `init`,
        # no `--migrate` needed -- the same line the stale `/mcp/<bank>` URL
        # already sits on: the entry is unambiguously mnemo's own and the fix
        # is unambiguous, so demanding a flag would only strand every adopted
        # project on a refusal. What `--migrate` is for is the *stdio*
        # generations below, whose shape mnemo will not rewrite unasked.
        del servers[_LEGACY_INSTANCE]
        renamed = True
        log.append(f"  {label:<20} renamed mcpServers.{_LEGACY_INSTANCE} "
                   f"-> {_INSTANCE}")
    elif legacy_key is not None and _is_legacy_mcp(legacy_key) is not None:
        if not migrate:
            raise _Refuse(
                f"{path}: 'mcpServers.{_LEGACY_INSTANCE}' is the legacy "
                f"{_is_legacy_mcp(legacy_key)} stdio form under mnemo's former "
                f"key, and calls `mnemo mcp` — a subcommand that no longer "
                f"exists.\n"
                f"      found:    {json.dumps(legacy_key)}\n"
                f"      expected: {_redacted(target)} under '{_INSTANCE}'\n"
                f"      (left untouched — re-run with `--migrate`)")
        del servers[_LEGACY_INSTANCE]
        renamed = True
        log.append(f"  {label:<20} migrated mnemo server "
                   f"{_is_legacy_mcp(legacy_key)} -> http, renamed "
                   f"{_LEGACY_INSTANCE} -> {_INSTANCE}")
    # Anything else under `mnemo` is somebody else's server that happens to
    # share the name. Left exactly where it is, like any foreign key.

    existing = servers.get(_INSTANCE)
    if existing is not None and not _is_mnemo_http(existing):
        generation = _is_legacy_mcp(existing)
        if generation is None:
            raise _Refuse(
                f"{path}: 'mcpServers.{_INSTANCE}' exists in a shape mnemo "
                f"does not recognise.\n"
                f"      found:    {json.dumps(existing)}\n"
                f"      expected: {_redacted(target)}\n"
                f"      (left untouched — resolve via the adopt skill)")
        if not migrate:
            raise _Refuse(
                f"{path}: 'mcpServers.{_INSTANCE}' is the legacy "
                f"{generation} stdio form, which calls `mnemo mcp` — a "
                f"subcommand that no longer exists.\n"
                f"      found:    {json.dumps(existing)}\n"
                f"      expected: {_redacted(target)}\n"
                f"      (left untouched — re-run with `--migrate`)")
        log.append(f"  {label:<20} migrated mnemo server {generation} -> http")
    elif existing == target and not renamed:
        log.append(f"  {label:<20} mnemo server already present")
        return None
    elif existing == target:
        # Both keys were present; the new one is already right, but the old
        # one has just been dropped, so this is still a write.
        log.append(f"  {label:<20} dropped the superseded duplicate")
    elif existing is not None:
        log.append(f"  {label:<20} updated mcpServers.{_INSTANCE}")
    elif not renamed:
        # After a rename the "renamed X -> Y" line already said this; a second
        # line reads as two entries having appeared.
        log.append(f"  {label:<20} +mcpServers.{_INSTANCE}")

    # Additive: keep every other server and key, replace only ours.
    servers[_INSTANCE] = target
    data["mcpServers"] = servers
    return _dump_json(data)


def _plan_mcp(path: Path, log: list[str], *, token: str = "",
              migrate: bool = False) -> str | None:
    """Return the new `.mcp.json` text, or None if already correct.

    The **no-template** form: the entry carries this bank's literal token, so
    the file must be git-ignored — `init_project` ensures that separately.

    Refuses on anything mnemo did not author. Rewrites a legacy mnemo entry
    only under `--migrate`, and only after recognising it as L1 or L2 — an
    unrecognised shape is always a refusal, never a guess. A superseded
    `/mcp/<bank>` HTTP entry is not a refusal: it is the current generation
    with a stale URL, so it is simply rewritten.

    ``(path, log)`` stay positional: the platform test calls this directly to
    inspect the wiring mnemo generates, and a signature churn there would
    break a check for no benefit. ``bank_name`` is gone — the token is the
    address now, so there is no name to put anywhere.
    """
    return _plan_servers(path, ".mcp.json", log, _mcp_server(token),
                         migrate=migrate)


def _plan_mcp_template(path: Path, log: list[str], *,
                       migrate: bool = False) -> str | None:
    """Return the new `.mcp.json.template` text, or None if already correct.

    The **template** form. Where a project carries one, `.mcp.json` is a
    generated artefact: `mcp-setup.sh` rewrites it wholesale from this file,
    so an entry written straight into `.mcp.json` is erased on the next run,
    silently. The entry therefore belongs here, with `{{VAR}}` placeholders,
    and the values live in `.mcp.env`, which git ignores.

    No token is needed to plan this file — that is the point of the
    placeholders, and it is why validation can run before a bank exists.
    """
    return _plan_servers(path, ".mcp.json.template", log,
                         _mcp_server_template(_VAR_INSTANCE), migrate=migrate)


# ------------------------------------------------- the template convention
#
# A project set up by the user-scope `project-mcp-setup` skill keeps `.mcp.json`
# **generated and git-ignored**; what git carries is `.mcp.json.template`,
# `.mcp.env.example` and `mcp-setup.sh`, which regenerates `.mcp.json` from
# `.mcp.env`. Detecting that convention is not a nicety: `mcp-setup.sh`
# overwrites `.mcp.json` wholesale, so an entry written straight into it is
# erased on the next run with no error and no trace. `init` therefore looks for
# the template first and, finding one, writes into the template layer instead.


def _read_text(path: Path) -> str:
    """Read a file **without** newline translation.

    `Path.read_text` would fold `\\r\\n` to `\\n` in memory, and `write_text`
    would then expand every `\\n` back to `\\r\\n` on Windows — so a file that
    was LF comes back CRLF, and the whole thing shows up as changed in git.
    Reading raw is what lets `_newline` see what the file actually uses.
    """
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except OSError:
        return ""


def _newline(text: str) -> str:
    """The line ending a file already uses, so an edit does not change it."""
    return "\r\n" if "\r\n" in text else "\n"


def _write(path: Path, text: str) -> None:
    """Write exactly the bytes we built — no platform translation.

    `newline=""` is the whole point: without it Python rewrites every `\\n` as
    `\\r\\n` on Windows, which turns a one-line addition to `.gitignore` into a
    whole-file diff and, far worse, puts a `\\r` at the end of every line of
    `mcp-setup.sh` — where `set -euo pipefail\\r` is a syntax error on any real
    Linux shell.
    """
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _env_comment(instance: str) -> str:
    return f"# {instance}"


def _plan_env(path: Path, log: list[str], label: str, prefix: str,
              values: dict[str, str], comment: str,
              *, update: bool = True, migrate: bool = False) -> str | None:
    """Set mnemo's own `NAME=value` lines in a `.env`-shaped file.

    Surgical in both directions: a variable that is already there has its
    value replaced **in place**, keeping its position and any comment above
    it; one that is missing is appended as a block at the end. Every foreign
    line is left exactly where it was, because this file is shared with every
    other MCP server the project uses.

    Replacing in place rather than appending a second definition matters — a
    later duplicate would win under `source`, so the file would work while
    saying two different things, which is the shape of bug nobody finds.
    """
    original = _read_text(path)
    eol = _newline(original)
    lines = original.splitlines()
    wanted = {f"{prefix}_{k}": v for k, v in values.items()}
    # Variables mnemo used to write and no longer does — `MNEMO_BANK` today.
    # Pruned rather than left: nothing substitutes them any more, so what
    # stays behind is a value that looks authoritative and is read by nothing.
    # Strictly mnemo's own keys, by prefix, so this cannot reach a variable
    # belonging to another server.
    #
    # **`--migrate` only.** A plain `init` is additive and never deletes —
    # that is the property the whole command is trusted for, and "it only
    # deleted its own key" is not a distinction worth spending it on.
    stale = ({f"{prefix}_{name}" for name in _RETIRED_ENV_VARS}
             if migrate else set())
    seen: set[str] = set()

    kept: list[str] = []
    for line in lines:
        name, sep, _ = line.partition("=")
        key = name.strip()
        if sep and key in stale:
            log.append(f"  {label:<20} -{key} (no longer used)")
            continue
        if sep and key in wanted:
            seen.add(key)
            # `update=False` for `.mcp.env.example`: that file is a git-tracked
            # document about what the variables are, and whatever a human wrote
            # next to a name there is theirs, not a value to correct.
            replacement = f"{key}={wanted[key]}"
            kept.append(replacement if update else line)
            continue
        kept.append(line)
    lines = kept

    missing = {k[len(prefix) + 1:]: v for k, v in wanted.items()
               if k not in seen}
    if missing:
        if lines and lines[-1].strip():
            lines.append("")
        # Extended line by line rather than appended as one pre-joined block:
        # a block joined with `\n` and dropped into a CRLF file would carry LF
        # inside itself and CRLF around it.
        lines.extend(comment.splitlines())
        lines.extend(f"{prefix}_{k}={v}" for k, v in missing.items())

    # Rejoined with the file's OWN line ending, not with `\n`: a `.mcp.env`
    # edited on Windows is CRLF, and converting it wholesale would turn a
    # three-line addition into a whole-file diff in somebody's repository.
    text = eol.join(lines) + eol if lines else ""
    if text == original:
        log.append(f"  {label:<20} mnemo variables already present")
        return None
    log.append(f"  {label:<20} "
               + ("+" if missing else "updated ")
               + ", ".join(sorted(wanted)))
    return text


# The variables mnemo owns in a template project's `.mcp.env`, in the order
# they are written.
_ENV_VARS = ("PORT", "TOKEN")
# Variables mnemo wrote in an earlier generation and now prunes. `BANK` went
# with the URL path segment: the token addresses the bank, so a name in the
# environment could only ever be a second opinion — and one that nothing
# substitutes, since `{{MNEMO_BANK}}` no longer appears in the template.
_RETIRED_ENV_VARS = ("BANK",)

# The one comment `.mcp.env.example` carries for mnemo, verbatim from the
# skill's `templates/mnemo.example` — it is what tells the next person where
# the blank value comes from.
_ENV_EXAMPLE_COMMENT = (
    "# {instance} — the cabinet (`mnemo ui`) shows the bank's token, or read "
    "it from\n"
    "# ~/.claude/mnemo/state/banks.json"
)


def _sed_line(prefix: str, name: str) -> str:
    """One `sed -e` substitution, in the exact shape mnemo writes it.

    One function so that what `--migrate` recognises as "ours, verbatim" and
    what `init` writes can never drift apart — a removal rule matched against
    a *different* string from the one that produced the line is a removal rule
    that quietly stops matching.
    """
    return f'  -e "s|{{{{{prefix}_{name}}}}}|${{{prefix}_{name}}}|g" \\'


def _sed_lines(prefix: str) -> list[str]:
    """The substitutions `mcp-setup.sh` needs, one per placeholder."""
    return [_sed_line(prefix, name) for name in _ENV_VARS]


def _plan_setup_sh(path: Path, log: list[str], prefix: str,
                   notes: list[str], *, migrate: bool = False) -> str | None:
    """Reconcile mnemo's `sed -e` lines inside the script's single invocation.

    Both directions, because a generation change needs both: missing
    substitutions are inserted, and — under `--migrate` — ones mnemo used to
    write and no longer does (`{{MNEMO_BANK}}`) are removed. A leftover would
    be a harmless no-op today and a lie in the file: it says a placeholder
    exists that the template no longer contains.

    **This script is not mnemo's file.** The user's `project-mcp-setup` skill
    wrote it; mnemo only ever appended lines to it. So removal matches the
    **exact line mnemo itself would have written**, modulo surrounding
    whitespace — never "any line mentioning our placeholder". A line that has
    since been edited by hand is left exactly where it is and reported,
    because a hand-edited line is someone's intent and guessing at it is how a
    project ends up unable to regenerate its own `.mcp.json`. Nothing else in
    the script is reflowed, reordered or rewritten.

    Returns the new text, ``None`` if nothing needs doing, and raises nothing
    — a script whose sed invocation cannot be located is reported by the
    caller and left untouched.

    The anchor is the `"$TEMPLATE"` argument, which is the last thing in that
    invocation by construction (`templates/setup.sh.head`): every `-e` line
    goes before it.
    """
    original = _read_text(path)
    if not original:
        return None
    eol = _newline(original)
    lines = original.splitlines()

    if migrate:
        # The exact text mnemo writes for a retired variable. Compared
        # stripped, so indentation may differ; anything else may not.
        exact = {_sed_line(prefix, name).strip() for name in _RETIRED_ENV_VARS}
        tokens = [f"{{{{{prefix}_{name}}}}}" for name in _RETIRED_ENV_VARS]
        kept, hand_edited = [], []
        for line in lines:
            if any(token in line for token in tokens):
                if line.strip() in exact:
                    continue                    # ours, verbatim — remove it
                hand_edited.append(line.strip())
            kept.append(line)
        dropped = len(lines) - len(kept)
        if dropped:
            log.append(f"  mcp-setup.sh         -{dropped} retired "
                       f"substitution(s) ({', '.join(tokens)})")
        for line in hand_edited:
            notes.append(
                f"mcp-setup.sh has a {', '.join(tokens)} line mnemo did not "
                f"write:\n      {line}\n"
                f"  It substitutes a placeholder the template no longer "
                f"contains, but it has been edited by hand, so it was LEFT "
                f"ALONE. Remove it yourself if it is dead."
            )
        lines = kept
    else:
        dropped = 0

    wanted = [line for line in _sed_lines(prefix)
              if line.strip() not in {existing.strip() for existing in lines}]
    if wanted:
        anchor = next(
            (i for i, line in enumerate(lines)
             if line.strip().startswith('"$TEMPLATE"')),
            None,
        )
        if anchor is None:
            # No recognisable invocation. Report what was already removed as
            # not-done too: the caller prints the lines to add by hand, and
            # half-editing the script would be worse than not touching it.
            return None
        lines[anchor:anchor] = wanted
        log.append(f"  mcp-setup.sh         +{len(wanted)} sed substitution(s) "
                   f"for {prefix}_*")
    elif not dropped:
        log.append("  mcp-setup.sh         sed lines already correct")
        return None

    # The script's own line ending. Rejoining an LF script with CRLF is not
    # cosmetic here: `set -euo pipefail\r` is a syntax error on Linux, so this
    # would hand the project back a `mcp-setup.sh` that no longer runs.
    return eol.join(lines) + eol


def _plan_gitignore(path: Path, log: list[str],
                    needed: Iterable[str]) -> str | None:
    """Ensure each entry is ignored, adding only the ones that are missing.

    Never reorders and never rewrites: existing lines are untouched and the
    missing ones are appended under one header. A `.gitignore` is a
    human-curated, git-tracked file — the smallest possible edit is the only
    acceptable one.
    """
    original = _read_text(path)
    eol = _newline(original)
    present = {line.strip() for line in original.splitlines()}
    missing = [entry for entry in needed if entry not in present]
    if not missing:
        return None
    body = original
    if body and not body.endswith(("\n", "\r")):
        body += eol
    if body:
        body += eol
    body += "# mnemo — generated wiring holds a per-bank token" + eol
    body += "".join(entry + eol for entry in missing)
    log.append(f"  .gitignore           +{', '.join(missing)}")
    return body


# --------------------------------------------------------- git index probe
#
# "Is `.mcp.json` tracked?" has to be answered before `init` writes a literal
# token into it, and it is answered by **reading** `.git/index` — never by
# running git. Two reasons: `init` must never mutate a repository (the fix is
# `git rm --cached`, and that is the user's call), and a read that needs no git
# binary on PATH works in the environments where this actually matters.


def _git_worktree(proj: Path) -> tuple[Path, Path] | None:
    """``(work_tree_root, git_dir)`` for the repo containing ``proj``."""
    node = proj.resolve()
    while True:
        marker = node / ".git"
        if marker.is_dir():
            return node, marker
        if marker.is_file():
            # A worktree or a submodule: the file holds `gitdir: <path>`.
            text = _read_text(marker).strip()
            if not text.startswith("gitdir:"):
                return None
            target = Path(text.split(":", 1)[1].strip())
            return node, target if target.is_absolute() else (node / target)
        if node.parent == node:
            return None
        node = node.parent


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    """Git's offset-encoded variable-length integer. Returns ``(value, pos)``.

    Used only by index v4. Not the same encoding as the one in packfiles: each
    continuation adds one *before* shifting, which is what makes the encoding
    prefix-free.
    """
    byte = data[pos]
    pos += 1
    value = byte & 0x7F
    while byte & 0x80:
        byte = data[pos]
        pos += 1
        value = ((value + 1) << 7) | (byte & 0x7F)
    return value, pos


def _index_paths(index: Path) -> set[str] | None:
    """Every path in a git index. ``set()`` if there is no index at all;
    ``None`` only when one exists and cannot be parsed.

    That three-way answer is the point, and the caller depends on it: a fresh
    `git init` with nothing staged has **no** `.git/index` file, and reading
    that as "unknown" rather than "nothing is tracked" would make `init`
    refuse in a brand-new repository, which is exactly where it is most likely
    to be run.

    Layout: a 12-byte header, then per entry 62 fixed bytes — 64 with the
    extended flag — a path, and NUL padding to the next multiple of eight.

    **v4 is parsed too**, not skipped. It was tempting to answer "unknown" for
    it, since `index.version=4` is uncommon — but the caller now *refuses* on
    unknown, and a format some people genuinely configure would then make
    `init` unusable for them with no way forward. v4 differs in exactly two
    ways: paths are prefix-compressed against the previous entry (a varint
    saying how many trailing bytes to strip, then the new suffix), and entries
    are not padded.

    **Known limitation — a sparse index.** With ``index.sparse`` on, git may
    store one directory entry in place of every file entry beneath it, so a
    tracked file can be absent from the paths collected here. Recorded rather
    than handled, for two reasons. It needs the project to sit *outside* the
    sparse cone, and a directory outside the cone is not checked out — `init`
    would be running in a folder that does not exist on disk; the repository
    root is always in the cone. And it fails in the safe direction: a hidden
    entry reads as "not tracked", which is the branch that writes
    ``.gitignore`` for a file that was never in git anyway.

    That safe direction is the property that earns this parser its keep, and
    it is worth stating plainly: every failure mode routes to ``None``, and
    the caller refuses on ``None``, so this can only ever be too cautious —
    never permissive. ``set()`` comes back from exactly one condition, the
    absence of an index file, which is a fact rather than an inference.
    Answering "is it tracked?" by shelling out to git would be shorter, and
    would not have violated the never-mutate rule either (a read is a read) —
    but it would trade a failure mode that refuses for one that depends on a
    binary being present and on its output being parsed correctly.
    """
    try:
        data = index.read_bytes()
    except FileNotFoundError:
        return set()           # a repo with nothing staged yet
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"DIRC":
        return None
    version = int.from_bytes(data[4:8], "big")
    if version not in (2, 3, 4):
        return None
    count = int.from_bytes(data[8:12], "big")
    out: set[str] = set()
    pos = 12
    previous = ""
    try:
        for _ in range(count):
            if pos + 62 > len(data):
                return None
            flags = int.from_bytes(data[pos + 60:pos + 62], "big")
            base = 62 + (2 if flags & 0x4000 else 0)
            start = pos + base
            if version < 4:
                end = data.index(b"\x00", start)
                path = data[start:end].decode("utf-8", errors="replace")
                total = base + (end - start)
                pos += total + (8 - total % 8)
            else:
                strip, start = _varint(data, start)
                end = data.index(b"\x00", start)
                suffix = data[start:end].decode("utf-8", errors="replace")
                path = (previous[:len(previous) - strip] if strip else previous) + suffix
                pos = end + 1
            out.add(path)
            previous = path
    except (IndexError, ValueError):
        # A truncated or otherwise malformed index. "Unknown", never "empty":
        # answering "nothing is tracked" from a file we failed to read is how
        # a token ends up in a tracked file.
        return None
    return out


def _refuse_if_tracked(proj: Path, name: str) -> None:
    """Refuse to write a literal bank token into a git-tracked file.

    **A refusal, never a warning, and the asymmetry is the whole reason.** A
    refusal costs one command to undo. A token committed into a tracked file
    cannot be undone in any useful sense: by the time anyone notices, it is in
    somebody else's clone. So "warn and write it anyway" is not a weaker
    version of this rule, it is the absence of it.

    `None` — an index that exists but will not parse — refuses too. Guessing
    "probably not tracked" is the one guess whose wrong answer is the
    unrecoverable one. A repository with nothing staged yet has no index file
    at all, and `_index_paths` answers ``set()`` for that, so a brand-new repo
    is not caught by this.

    `init` never runs git, in either direction: it names the command and
    stops. What to do about the file is the user's call.
    """
    tracked = _git_tracked(proj, name)
    if tracked:
        raise _Refuse(
            f"{proj / name} is TRACKED by git, and `mnemo init` would write a "
            f"literal bank token into it.\n"
            f"      Untrack it first:  git rm --cached {name}\n"
            f"      then re-run `mnemo init`.\n"
            f"      (`init` never runs git — that command is yours to run.)")
    if tracked is None:
        raise _Refuse(
            f"cannot tell whether {proj / name} is tracked by git: an index "
            f"exists at {proj / '.git'} but could not be parsed.\n"
            f"      `mnemo init` would write a literal bank token into that "
            f"file, so this is refused rather than guessed.\n"
            f"      Check with:  git ls-files --error-unmatch {name}")


def _git_tracked(proj: Path, name: str) -> bool | None:
    """Is ``proj/name`` in the index? ``None`` = could not tell."""
    found = _git_worktree(proj)
    if found is None:
        return False           # not a repository: nothing can be tracked
    work_tree, git_dir = found
    paths = _index_paths(git_dir / "index")
    if paths is None:
        return None
    try:
        rel = (proj.resolve() / name).relative_to(work_tree).as_posix()
    except ValueError:
        return None
    return rel in paths


def _is_mnemo_cmd(command: object, subcmd: str) -> bool:
    """A hook command that targets `mnemo <subcmd>` (any launcher path)."""
    if not isinstance(command, str):
        return False
    toks = command.split()
    return bool(toks) and "mnemo" in command and toks[-1] == subcmd


def _drop_retired_hooks(hooks: dict, path: Path, log: list[str],
                        retired: tuple[tuple[str, str], ...]) -> bool:
    """`--migrate` only: unwire hooks this generation no longer writes.

    Surgical by construction — it deletes a hook entry only when
    `_is_mnemo_cmd` says mnemo wrote it, and removes the surrounding group
    only once that group is empty. A foreign hook sharing the same event is
    left exactly where it was.
    """
    changed = False
    for event, subcmd in retired:
        arr = hooks.get(event)
        if not isinstance(arr, list):
            continue
        kept_groups = []
        for grp in arr:
            if not isinstance(grp, dict):
                kept_groups.append(grp)
                continue
            entries = grp.get("hooks")
            if not isinstance(entries, list):
                kept_groups.append(grp)
                continue
            kept = [h for h in entries
                    if not (isinstance(h, dict)
                            and _is_mnemo_cmd(h.get("command"), subcmd))]
            if len(kept) != len(entries):
                changed = True
                log.append(f"  settings.json        -hooks.{event} "
                           f"(mnemo {subcmd}, no longer wired by default)")
            if kept:
                grp = dict(grp, hooks=kept)
                kept_groups.append(grp)
            # An emptied group is dropped entirely rather than left as a
            # matcher with no hooks, which Claude Code would still evaluate.
        if kept_groups:
            hooks[event] = kept_groups
        elif event in hooks:
            del hooks[event]
    return changed


def _plan_settings(path: Path, log: list[str],
                   migrate: bool = False) -> str | None:
    """Return the new `.claude/settings.json` text, or None if already
    correct.

    `init` never *writes* a hook any more, so this function has exactly one
    job left: with `--migrate`, unwire the ones earlier generations wrote.
    Without it, it returns None without reading a thing — a default `init`
    leaves `settings.json` alone, and never creates one to say nothing.
    """
    if not migrate:
        return None

    data = _load_json(path)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
    elif not isinstance(hooks, dict):
        raise _Refuse(f"{path}: 'hooks' is not an object; left untouched")

    if not _drop_retired_hooks(hooks, path, log, _RETIRED_HOOKS):
        return None
    data["hooks"] = hooks
    return _dump_json(data)


def _seed_tree(claude: Path, log: list[str]) -> None:
    """Seed the bare minimum, only when absent — never invent memory.

    A one-line `MEMORY.md` anchor and the binding memory rule. No empty
    `logs/`/`agent-memory/` structure: those appear when something is
    actually written. Per-agent memory stubs are the adopt skill's job,
    created after the agent roster is decided.
    """
    mem = claude / "memory"
    rules = claude / "rules"
    for d in (mem, rules):
        if not d.exists():
            d.mkdir(parents=True)
            log.append(f"  created              {d}")

    index = mem / "MEMORY.md"
    if not index.exists():
        _write(index, f"# Memory Index — {claude.parent.name}\n")
        log.append(f"  created              {index} (one-line anchor)")
    else:
        log.append(f"  kept                 {index} (already present)")

    rule = rules / "mnemo-memory.md"
    if not rule.exists():
        _write(rule, _MEMORY_RULE)
        log.append(f"  created              {rule}")
    else:
        # The one file here mnemo may rewrite, and only while the bytes prove
        # nobody else has touched it. `MEMORY.md` above is curated content and
        # is never reconsidered; this is mnemo's own instruction text, and a
        # project left on an old redaction is a project running old rules.
        state = _rule_state(rule)
        if state == "current":
            log.append(f"  kept                 {rule} (already current)")
        elif state == "stale":
            _write(rule, _MEMORY_RULE)
            log.append(f"  updated              {rule} (superseded redaction)")
        else:
            log.append(
                f"  kept                 {rule} (edited here — left alone; "
                f"mnemo's current text differs)"
            )


class AdoptedProject:
    """A project on this machine that carries mnemo wiring.

    `migrate` records whether `init` alone can fix it. A stdio entry or a
    retired hook is a shape mnemo will not rewrite unasked (§11.3), so those
    need `--migrate`; anything else is mnemo's own current-generation wiring
    and a plain `init` re-points it.
    """

    __slots__ = ("root", "findings", "migrate", "token")

    def __init__(self, root: Path, findings: list[str], migrate: bool,
                 token: str | None = None) -> None:
        self.root = root
        self.findings = findings
        self.migrate = migrate
        # The literal token the project presents, when it writes one at all.
        # `None` covers both "no literal `.mcp.json`" and the template layer,
        # where the file holds `{{MNEMO_TOKEN}}` and the real value lives in
        # `.mcp.env` — nothing here should go reading a secrets file.
        self.token = token

    def command(self) -> str:
        flag = " --migrate" if self.migrate else ""
        return f'mnemo init{flag} --root "{self.root}"'


def known_project_roots() -> list[Path]:
    """Absolute project paths Claude Code keeps a per-project record for.

    Read from `~/.claude.json`, whose `projects` object is **keyed by the
    absolute path**. The sibling `~/.claude/projects/` directory is the
    obvious-looking source and is unusable: its folder names flatten `:`,
    `\\` and `_` all to `-`, so `E--work-projects-other-mnemo` cannot be
    decoded back to a path — the separators are gone, not encoded.

    Best-effort by design. This backs a diagnostic, and a machine with no
    Claude Code config is not an error; it just has nothing to list.
    """
    path = Path.home() / ".claude.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    projects = doc.get("projects")
    if not isinstance(projects, dict):
        return []
    roots, seen = [], set()
    for key in projects:
        if not isinstance(key, str) or not key.strip():
            continue
        root = Path(key)
        if not root.is_absolute():
            continue
        try:
            if not root.is_dir():
                continue                     # moved, deleted, unplugged drive
            canonical = root.resolve()
        except OSError:
            continue
        # The same tree reaches this list under both separators (`S:/x` and
        # `S:\x` are separate keys in that file), so canonicalise before
        # deduplicating or the same project is reported twice.
        if canonical in seen:
            continue
        seen.add(canonical)
        roots.append(canonical)
    return roots


def _mnemo_servers(path: Path) -> list[str]:
    """What mnemo-authored MCP entries a config file holds, described."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    found = []
    for key, entry in servers.items():
        legacy = _is_legacy_mcp(entry)
        if legacy:
            found.append(f"{path.name}: {key} is a {legacy} stdio entry")
        elif _is_mnemo_http(entry) and key in (_INSTANCE, _LEGACY_INSTANCE):
            found.append(f"{path.name}: {key} points at this machine's service")
    return found


_TOKEN_IN_URL = re.compile(r"[?&]token=([0-9a-fA-F]{32,})")


def _project_token(path: Path) -> str | None:
    """The literal bank token a `.mcp.json` presents, if it holds one.

    Only a real credential is returned: the regex wants hex, so the template
    layer's `{{MNEMO_TOKEN}}` and `${MNEMO_TOKEN}` never match. That is the
    point — a placeholder says nothing about whether the project can reach
    its bank, and the value behind it sits in `.mcp.env`, which is a secrets
    file and not something a diagnostic should open.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    servers = doc.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    for key, entry in servers.items():
        if key not in (_INSTANCE, _LEGACY_INSTANCE) or not _is_mnemo_http(entry):
            continue
        match = _TOKEN_IN_URL.search(entry.get("url", ""))
        if match:
            return match.group(1)
    return None


def _mnemo_hooks(path: Path) -> list[str]:
    """Retired mnemo hooks still wired in a settings file."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        return []
    found = []
    for event, subcmd in _RETIRED_HOOKS:
        for group in hooks.get(event, []) if isinstance(hooks.get(event), list) else []:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []) if isinstance(group.get("hooks"), list) else []:
                if isinstance(hook, dict) and _is_mnemo_cmd(hook.get("command"), subcmd):
                    found.append(f"settings.json: {event} -> {subcmd} hook")
    return found


def adopted_projects(roots: Iterable[Path] | None = None) -> list[AdoptedProject]:
    """Every known project carrying mnemo wiring, with the command that fixes it.

    The caller after a v2→v3 engine upgrade needs this because **v2 kept no
    registry**: nothing on disk records which projects used it. The indexes
    cannot answer it either — a v2 database has no `meta` table at all, and
    the filename is `sha1(root)`, which does not invert.

    Reports; never writes. Whether to run the printed commands is the user's
    call, in a working tree that is theirs and may well be dirty.
    """
    out = []
    for root in known_project_roots() if roots is None else roots:
        findings, migrate = [], False
        for name in (".mcp.json", ".mcp.json.template"):
            for note in _mnemo_servers(root / name):
                findings.append(note)
                if "stdio" in note:
                    migrate = True
        for note in _mnemo_hooks(root / ".claude" / "settings.json"):
            findings.append(note)
            migrate = True
        if findings:
            out.append(AdoptedProject(root, findings, migrate,
                                      _project_token(root / ".mcp.json")))
    return out


# `bank_name_for` used to live here: it worked out which name to put in the
# URL, and had to guess it before the bank was registered. Nothing needs a
# bank name in a file any more — the token is the address — so it went with
# the segment rather than staying as a helper with no caller.


# Stands in for the bank token while `init` is deciding whether it would
# refuse. No refusal depends on the token's value, so the validation pass can
# run before a bank has been registered — which is what keeps "writes NOTHING
# on refusal" true of the registry as well as of the project's files.
_TOKEN_PROBE = "\x00probe\x00"


class _Wiring:
    """What `init` decided to write, before anything is written."""

    def __init__(self) -> None:
        self.writes: list[tuple[Path, str]] = []
        self.log: list[str] = []
        self.notes: list[str] = []

    def add(self, path: Path, text: str | None) -> None:
        if text is not None:
            self.writes.append((path, text))


def _plan_wiring(proj: Path, *, token: str, migrate: bool) -> _Wiring:
    """Plan the MCP wiring for one project. Raises `_Refuse`, writes nothing.

    Two shapes, chosen by one fact — does `.mcp.json.template` exist:

    * **template project** — the entry goes into the template with `{{VAR}}`
      placeholders, the variables into `.mcp.env.example`, the real values into
      `.mcp.env` when that file is there, and the substitutions into
      `mcp-setup.sh`. `.mcp.json` is not touched at all: it is a build product,
      and `mcp-setup.sh` regenerates it.
    * **plain project** — the entry goes into `.mcp.json` with the literal
      token, and `.gitignore` gains `.mcp.json` if it does not already ignore
      it.

    Getting this wrong is silent, not loud: writing into `.mcp.json` where a
    template exists survives exactly until the next `bash mcp-setup.sh`.
    """
    wiring = _Wiring()
    prefix = _var_prefix(_VAR_INSTANCE)
    template = proj / ".mcp.json.template"

    if not template.exists():
        mcp_path = proj / ".mcp.json"
        _refuse_if_tracked(proj, ".mcp.json")
        # Not tracked — safe to write, and git must keep it that way.
        wiring.add(mcp_path, _plan_mcp(mcp_path, wiring.log, token=token,
                                       migrate=migrate))
        gitignore = proj / ".gitignore"
        wiring.add(gitignore,
                   _plan_gitignore(gitignore, wiring.log, [".mcp.json"]))
        return wiring

    wiring.add(template, _plan_mcp_template(template, wiring.log,
                                            migrate=migrate))

    example = proj / ".mcp.env.example"
    wiring.add(example, _plan_env(
        example, wiring.log, ".mcp.env.example", prefix,
        {"PORT": _api_port(), "TOKEN": ""},
        _ENV_EXAMPLE_COMMENT.format(instance=_INSTANCE),
        update=False, migrate=migrate,
    ))

    # `.mcp.env` holds the real values and is git-ignored. Written only when
    # it already exists: creating it would be creating a secrets file the
    # project never asked for, and `mcp-setup.sh` already tells the user to
    # copy the example when it is missing.
    env = proj / ".mcp.env"
    if env.exists():
        # Same rule as `.mcp.json` in the other branch, for the same reason:
        # this is where the literal token goes in a template project. The
        # lead's rule was written about `.mcp.json`, but it is a rule about
        # literal bank tokens in tracked files, and this is the other file
        # that gets one.
        _refuse_if_tracked(proj, ".mcp.env")
        wiring.add(env, _plan_env(
            env, wiring.log, ".mcp.env", prefix,
            {"PORT": _api_port(), "TOKEN": token},
            _env_comment(_INSTANCE), migrate=migrate,
        ))
    else:
        wiring.notes.append(
            ".mcp.env does not exist, so the real values were not written.\n"
            "  Run:  cp .mcp.env.example .mcp.env  then re-run `mnemo init`"
        )

    setup = proj / "mcp-setup.sh"
    if not setup.exists():
        wiring.notes.append(
            "there is a .mcp.json.template but no mcp-setup.sh, so nothing "
            "regenerates .mcp.json.\n"
            "  Add these lines to whatever does:\n"
            + "\n".join(f"    {line}" for line in _sed_lines(prefix))
        )
    else:
        planned = _plan_setup_sh(setup, wiring.log, prefix, wiring.notes,
                                 migrate=migrate)
        # `None` means either "already correct" or "no anchor to insert at",
        # and only the second needs saying. The placeholder's presence is what
        # separates them.
        if planned is None and f"{{{{{prefix}_TOKEN}}}}" not in _read_text(setup):
            wiring.notes.append(
                "mcp-setup.sh has no recognisable `sed … \"$TEMPLATE\"` "
                "invocation, so it was left untouched.\n"
                "  Add these lines to its sed call by hand — WITHOUT them the "
                "script still exits 0\n"
                "  and writes a .mcp.json whose URL literally says "
                "{{MNEMO_TOKEN}}, which never connects:\n"
                + "\n".join(f"    {line}" for line in _sed_lines(prefix))
            )
        wiring.add(setup, planned)

    return wiring


def _register_bank(bank_root: Path, report: list[str]) -> str:
    """Register the project's memory root and return its **token**.

    The name is reported to the human and then dropped: no file `init` writes
    contains it any more. The token is the address.

    The service is asked first, because it is the one thing that can also
    *queue the first index*. When it is down the registry is written directly
    — it is a locked JSON document built for exactly that — so the wiring
    still gets a real bank name and a real token, and the backend picks the
    bank up through reconcile-on-start (§9.6). Wiring that is complete but
    not yet indexed is worth far more than wiring with a blank token in it.

    The token always comes from the registry rather than over HTTP: it is the
    same value either way, and one code path has one failure mode.
    """
    from . import registry
    from .client import ApiFailure, Client, ServiceDown

    bank_root.mkdir(parents=True, exist_ok=True)
    try:
        info = Client(timeout=5.0).add_bank(str(bank_root))
        report.append(f"  bank                 registered as {info['name']}; "
                      f"indexing queued")
    except ServiceDown:
        try:
            bank = registry.add(bank_root)
            report.append(f"  bank                 registered as {bank.name} "
                          f"— backend is down, so it will be indexed when "
                          f"the service next starts")
        except registry.BankExists:
            report.append("  bank                 already registered — "
                          "backend is down, nothing queued")
    except ApiFailure as exc:
        if exc.code == "bank_exists":
            report.append("  bank                 already registered")
        else:
            report.append(f"  bank                 not registered — "
                          f"{exc.code}: {exc.message}")

    bank = registry.resolve(str(bank_root))
    return registry.token_for(bank.id)


def init_project(root: str | None, *, migrate: bool = False) -> int:
    """Wire mnemo into a project. Returns 0 on success, 1 on refusal
    (in which case NOTHING was written).

    **No hook is written, and there is no flag that makes one appear.** The
    discipline is carried by `.claude/rules/mnemo-memory.md` alone. With
    `--migrate`, hooks earlier generations wrote are unwired.

    Two passes, and the split is what keeps the refusal guarantee honest. The
    first plans everything with a probe token purely to find out whether
    anything would refuse; only once nothing does are the seed, the bank and
    its token created, and only then is the same plan rendered for real. No
    refusal depends on the token's value, so the two passes always agree about
    whether to stop.
    """
    paths = resolve(root)
    proj = paths.root
    settings_path = proj / ".claude" / "settings.json"

    # Pass 1 — validation only. Nothing is written and no bank is registered.
    try:
        _plan_wiring(proj, token=_TOKEN_PROBE, migrate=migrate)
        _plan_settings(settings_path, [], migrate)
    except _Refuse as exc:
        print(f"mnemo init: refused — {exc}")
        print("mnemo init: NOTHING was written.")
        return 1

    # Nothing below can refuse. Seed first, because the bank root has to exist
    # before it can be registered.
    log: list[str] = []
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_tree(proj / ".claude", log)

    report: list[str] = []
    try:
        token = _register_bank(proj / ".claude" / "memory", report)
    except Exception as exc:  # noqa: BLE001 - no bank means no usable wiring
        print(f"mnemo init: project = {proj}")
        for line in log + report:
            print(line)
        print(f"mnemo init: could not register the bank ({exc}); the MCP "
              f"wiring needs its token, so it was NOT written.")
        return 1

    # Pass 2 — the same plan, rendered with the real token.
    wiring = _plan_wiring(proj, token=token, migrate=migrate)
    new_settings = _plan_settings(settings_path, wiring.log, migrate)
    for path, text in wiring.writes:
        _write(path, text)
    if new_settings is not None:
        _write(settings_path, new_settings)

    print(f"mnemo init: project = {proj}")
    for line in log + wiring.log + report:
        print(line)
    for note in wiring.notes:
        print(f"  NOTE                 {note}")
    if (proj / ".mcp.json.template").exists():
        # The second sentence costs one line and covers a failure that is
        # otherwise silent. `init` writes the `-e` lines itself, so this run
        # is safe — but the same fragment gets hand-pasted (from the cabinet,
        # from the skill's example), and `sed` copies an unmatched
        # `{{PLACEHOLDER}}` through verbatim: the script prints its success
        # tick, exits 0, and leaves a `.mcp.json` that simply never connects.
        # Saying what that looks like is what makes it findable.
        print("mnemo init: the entry went into .mcp.json.template — run "
              "`bash mcp-setup.sh` to regenerate .mcp.json.")
        print("            If a URL in .mcp.json still shows {{MNEMO_TOKEN}} "
              "afterwards, mcp-setup.sh\n"
              "            is missing that placeholder's `-e` line — sed "
              "copies unmatched ones through\n"
              "            and still exits 0. `mnemo init` adds them; "
              "hand-pasting the fragment does not.")
    print("mnemo init: done. Review the changes, then commit them "
          "(and trust the project in Claude Code).")
    return 0
