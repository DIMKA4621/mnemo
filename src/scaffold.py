"""`mnemo init` — deterministic, idempotent project wiring.

This is a SAFE primitive, not a judgement call. It only ever:
  * creates, only when ABSENT, the bare minimum: a one-line
    `.claude/memory/MEMORY.md` anchor and the binding memory rule
    `.claude/rules/mnemo-memory.md` (never invents memory structure,
    never overwrites a curated or human-authored file);
  * merges strictly ADDITIVELY into `.mcp.json` / `.claude/settings.json`
    (adds only mnemo's own keys/hook groups, never touches or reorders
    foreign content);
  * refuses — writing NOTHING — if a *different* mnemo entry already
    exists, leaving that migration to the adopt skill (shown diff +
    confirmation). It never edits CLAUDE.md.

**No hook is written unless asked for.** `settings.json` is left alone by a
default `init` — the seeds are wired only by `--with-memory-hook` /
`--with-inject-hook`, and `--migrate` unwires whatever was not asked for.

The git-tracked invocation is portable by construction: hook seeds use the
shell form so `~` expands per-user at run time; the MCP server directly
executes the same logical launcher path after Claude Code expands
`${HOME}`. Installers provide that contract as a script on POSIX and a
real executable on Windows. No machine-specific path is written into git.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from . import config
from .config import resolve

# Portable launcher reference. Each teammate's own $HOME resolves at run
# time — nothing machine-specific lands in git. Needed only by the hook seeds;
# the MCP wiring no longer uses it at all.
_LAUNCHER = "~/.claude/mnemo/bin/mnemo"


def _mcp_server(bank_name: str) -> dict:
    """The v3 MCP entry (§10.4): HTTP, and nothing machine-specific.

    Both moving parts are deliberate. The **bank is a name**, not a
    `bank_id` — an id is `sha1(absolute path)[:16]`, correct on exactly one
    machine with one checkout, and meaningless to a colleague who clones
    elsewhere. The **token is a `${MNEMO_API_TOKEN}` placeholder**, exported
    by the installer, so a secret never lands in a git-tracked file.

    Both ride in the URL rather than in headers, because a header depends on
    Claude Code forwarding `headers` for `type: http` — and if it does not,
    authentication fails, not just addressing. The header form is still
    accepted by the backend as an override; it is simply not depended on.
    """
    port = getattr(config, "API_PORT", None) or os.environ.get(
        "MNEMO_API_PORT", "8918"
    )
    # The name is percent-encoded: it is a human label, and on this project's
    # own machine those contain spaces and Cyrillic. A raw space makes the
    # URL invalid outright, and a raw non-ASCII byte is not portable across
    # clients. `safe=""` also encodes `/` and `+`, which would otherwise be
    # read as a path separator and as a space respectively.
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/mcp/{quote(bank_name, safe='')}"
               f"?token=${{MNEMO_API_TOKEN}}",
        "headers": {
            "Authorization": "Bearer ${MNEMO_API_TOKEN}",
            "X-Mnemo-Bank": bank_name,
        },
    }


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

# Hook seeds. Shell form (a bare `command` string, no `args`) so the shell
# expands `~` at run time.
#
# **`init` writes NO hook unless asked** (design #15). The mechanism stays
# whole — that is what a seed is — but nothing lands in somebody's
# git-tracked `settings.json` by default, because the primary access to memory
# is MCP and the discipline is carried by the rule, not by an injection.
#
# The two seeds differ in kind, and the difference is the reason only one of
# them is recommended:
#
#   * `memory-hook` hands over the bank's `MEMORY.md` plus the layout — a
#     **map**, which says "search for the detail". It cannot be mistaken for
#     having searched.
#   * `hook-inject` hands over top-N sections **before** the task is even
#     stated, which reads as memory already gathered. That false sense of
#     coverage is why it is off.
#
# seed name -> (event, subcommand, hook group)
_HOOK_SEEDS: dict[str, tuple[str, str, dict]] = {
    "memory": ("SessionStart", "memory-hook", {
        "hooks": [
            {"type": "command",
             "command": f"{_LAUNCHER} memory-hook", "timeout": 10},
        ],
    }),
    "inject": ("UserPromptSubmit", "hook-inject", {
        "hooks": [
            {"type": "command",
             "command": f"{_LAUNCHER} hook-inject", "timeout": 30},
        ],
    }),
}

# Hooks earlier generations wrote that `--migrate` removes. Pairs, not a dict
# keyed by event: `SessionStart` appears twice with different subcommands (v2's
# `ingest`, v3's `memory-hook` seed), and a dict would silently lose one.
#
#   * v2: `SessionStart -> ingest`, `PostToolUse -> hook-postedit` — both did
#     work the watcher now does, and did it *inside* the session, which is the
#     blocking behaviour v3 exists to remove.
#   * early v3: `UserPromptSubmit -> hook-inject` was wired automatically.
#     Now it is opt-in, so `--migrate` unwires it unless it was asked for.
_RETIRED_HOOKS: tuple[tuple[str, str], ...] = (
    ("SessionStart", "ingest"),
    ("PostToolUse", "hook-postedit"),
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

## Searching — the part that is not optional

The MCP tool is **`search`**. Narrow with `path_prefix` when you know
roughly where to look (`logs`, `topics`, `agents/reviewer`); leave it out to
search the whole bank. `tree` shows the layout with each file's
headings.

**You have not consulted memory until you have called `search` in this
session, for this task.** Text that happens to be in your context is not a
search result: it may be stale, it may be about something else, and it is not
evidence that anything was checked. Do not reason from "I think I already have
this".

Search **before**:

- planning, or proposing an approach;
- changing architecture, an interface, or a schema;
- debugging anything that is not a one-line typo;
- answering "why is this like this?" or "did we try X?";
- re-investigating anything that smells like it was decided before.

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


def _plan_mcp(path: Path, log: list[str], *, bank_name: str | None = None,
              migrate: bool = False) -> str | None:
    """Return the new `.mcp.json` text, or None if already correct.

    Refuses on anything mnemo did not author. Rewrites a legacy mnemo entry
    only under `--migrate`, and only after recognising it as L1 or L2 — an
    unrecognised shape is always a refusal, never a guess.

    ``(path, log)`` stay positional: the platform test calls this directly to
    inspect the wiring mnemo generates, and a signature churn there would
    break a check for no benefit.
    """
    bank_name = bank_name or bank_name_for(path.parent)
    data = _load_json(path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    elif not isinstance(servers, dict):
        raise _Refuse(f"{path}: 'mcpServers' is not an object; left untouched")

    target = _mcp_server(bank_name)
    existing = servers.get("mnemo")
    if existing == target:
        log.append("  .mcp.json            mnemo server already present")
        return None

    if existing is not None:
        generation = _is_legacy_mcp(existing)
        if generation is None:
            raise _Refuse(
                f"{path}: 'mcpServers.mnemo' exists in a shape mnemo does "
                f"not recognise.\n"
                f"      found:    {json.dumps(existing)}\n"
                f"      expected: {json.dumps(target)}\n"
                f"      (left untouched — resolve via the adopt skill)")
        if not migrate:
            raise _Refuse(
                f"{path}: 'mcpServers.mnemo' is the legacy {generation} "
                f"stdio form.\n"
                f"      found:    {json.dumps(existing)}\n"
                f"      expected: {json.dumps(target)}\n"
                f"      (left untouched — re-run with `--migrate`)")
        log.append(f"  .mcp.json            migrated mnemo server {generation}"
                   f" -> http")
    else:
        log.append("  .mcp.json            +mcpServers.mnemo")

    # Additive: keep every other server and key, replace only ours.
    servers["mnemo"] = target
    data["mcpServers"] = servers
    return _dump_json(data)


def _is_mnemo_cmd(command: object, subcmd: str) -> bool:
    """A hook command that targets `mnemo <subcmd>` (any launcher path)."""
    if not isinstance(command, str):
        return False
    toks = command.split()
    return bool(toks) and "mnemo" in command and toks[-1] == subcmd


def _retired_for(wanted: frozenset[str]) -> tuple[tuple[str, str], ...]:
    """`(event, subcmd)` pairs `--migrate` should unwire from this project.

    The retired v2 hooks, plus any **seed the caller did not ask for** — that
    is what makes `--migrate` bring an early-v3 project (auto-wired
    `hook-inject`) in line with "no hook unless asked", while
    `--with-inject-hook` keeps the one it was asked to keep.
    """
    return _RETIRED_HOOKS + tuple(
        (event, subcmd)
        for seed, (event, subcmd, _group) in _HOOK_SEEDS.items()
        if seed not in wanted
    )


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


def _plan_settings(path: Path, log: list[str], migrate: bool = False,
                   hooks_wanted: frozenset[str] = frozenset()) -> str | None:
    """Return the new `.claude/settings.json` text, or None if already
    correct. Raises _Refuse on a conflicting mnemo hook.

    With no seed requested and no `--migrate`, this returns None without
    reading a thing into the file — a default `init` leaves `settings.json`
    absent rather than creating one that says nothing.
    """
    if not hooks_wanted and not migrate:
        return None

    data = _load_json(path)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
    elif not isinstance(hooks, dict):
        raise _Refuse(f"{path}: 'hooks' is not an object; left untouched")

    changed = (
        _drop_retired_hooks(hooks, path, log, _retired_for(hooks_wanted))
        if migrate else False
    )
    for seed in sorted(hooks_wanted):
        event, subcmd, group = _HOOK_SEEDS[seed]
        desired_cmd = group["hooks"][0]["command"]
        arr = hooks.get(event)
        if arr is None:
            arr = []
        elif not isinstance(arr, list):
            raise _Refuse(f"{path}: 'hooks.{event}' is not an array; "
                          f"left untouched")

        found_exact = False
        for grp in arr:
            if not isinstance(grp, dict):
                continue
            for h in grp.get("hooks", []) or []:
                if not isinstance(h, dict):
                    continue
                cmd = h.get("command")
                if not _is_mnemo_cmd(cmd, subcmd):
                    continue
                if cmd == desired_cmd:
                    found_exact = True
                else:
                    raise _Refuse(
                        f"{path}: hooks.{event} already has a different "
                        f"mnemo {subcmd} hook.\n      found:    {cmd}\n"
                        f"      expected: {desired_cmd}\n"
                        f"      (left untouched — resolve via the adopt "
                        f"skill)")
        if found_exact:
            log.append(f"  settings.json        {event} hook already present")
            continue

        # Additive: foreign hook groups in this event stay as-is.
        arr.append(group)
        hooks[event] = arr
        log.append(f"  settings.json        +hooks.{event}")
        changed = True

    if not changed:
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
        index.write_text(f"# Memory Index — {claude.parent.name}\n",
                          encoding="utf-8")
        log.append(f"  created              {index} (one-line anchor)")
    else:
        log.append(f"  kept                 {index} (already present)")

    rule = rules / "mnemo-memory.md"
    if not rule.exists():
        rule.write_text(_MEMORY_RULE, encoding="utf-8")
        log.append(f"  created              {rule}")
    else:
        log.append(f"  kept                 {rule} (already present)")


def bank_name_for(proj: Path) -> str:
    """The name this project's bank is addressed by in `.mcp.json`.

    Prefers what the registry already calls it, so re-running `init` after a
    rename does not silently point the wiring at a bank that no longer
    answers to that name. Falls back to the same derivation `registry.add`
    would use, so the two agree before the bank is registered.
    """
    from . import registry

    claude = proj / ".claude"
    for candidate in (claude / "memory", claude, proj):
        try:
            return registry.resolve(str(candidate)).name
        except Exception:  # noqa: BLE001 - not registered yet is normal
            continue
    return registry.default_name(claude / "memory")


def init_project(root: str | None, *, migrate: bool = False,
                 hooks: Iterable[str] = ()) -> int:
    """Wire mnemo into a project. Returns 0 on success, 1 on refusal
    (in which case NOTHING was written).

    ``hooks`` names the seeds to wire (``"memory"``, ``"inject"``); empty — the
    default — writes none.
    """
    paths = resolve(root)
    proj = paths.root
    mcp_path = proj / ".mcp.json"
    settings_path = proj / ".claude" / "settings.json"
    wanted = frozenset(hooks)
    unknown = wanted - set(_HOOK_SEEDS)
    if unknown:
        print(f"mnemo init: unknown hook seed(s): {', '.join(sorted(unknown))}")
        return 1

    log: list[str] = []
    try:
        new_mcp = _plan_mcp(mcp_path, log, bank_name=bank_name_for(proj),
                            migrate=migrate)
        new_settings = _plan_settings(settings_path, log, migrate, wanted)
    except _Refuse as exc:
        print(f"mnemo init: refused — {exc}")
        print("mnemo init: NOTHING was written.")
        return 1

    # Validation passed — apply atomically (minimal seed + rule first,
    # then the additive JSON merges).
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    _seed_tree(proj / ".claude", log)
    if new_mcp is not None:
        mcp_path.write_text(new_mcp, encoding="utf-8")
    if new_settings is not None:
        settings_path.write_text(new_settings, encoding="utf-8")

    print(f"mnemo init: project = {proj}")
    for line in log:
        print(line)

    # Register the bank with the running service and let it build the index.
    # `init` never indexes inline any more: that was a multi-minute blocking
    # step inside somebody's terminal, and the service does it in the
    # background with a progress channel. If the backend is down, the wiring
    # is still the deliverable — say so and move on.
    from .client import ApiFailure, Client, ServiceDown

    bank_root = proj / ".claude" / "memory"
    try:
        client = Client(timeout=5.0)
        info = client.add_bank(str(bank_root))
        print(f"  bank                 registered as {info['name']}; "
              f"indexing queued")
    except ServiceDown:
        print("  bank                 NOT registered — backend is down.\n"
              "                       run `mnemo service start`, then "
              "`mnemo banks add .claude/memory`")
    except ApiFailure as exc:
        if exc.code == "bank_exists":
            print("  bank                 already registered")
        else:
            print(f"  bank                 not registered — {exc.code}: "
                  f"{exc.message}")

    print("mnemo init: done. Review the changes, then commit them "
          "(and trust the project in Claude Code).")
    return 0
