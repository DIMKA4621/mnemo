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

The git-tracked invocation is portable by construction: hooks use the
shell form so `~` expands per-user at run time; the MCP server directly
executes the same logical launcher path after Claude Code expands
`${HOME}`. Installers provide that contract as a script on POSIX and a
real executable on Windows. No machine-specific path is written into git.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import config
from .config import resolve

# Portable launcher reference. Each teammate's own $HOME resolves at run
# time — nothing machine-specific lands in git. Still needed for the
# `hook-inject` hook; the MCP wiring no longer uses it at all.
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
    return {
        "type": "http",
        "url": f"http://127.0.0.1:{port}/mcp/{bank_name}"
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

# One hook group per event. Shell form (a bare `command` string, no
# `args`) so the shell expands `~` at run time.
#
# v3 generates exactly ONE hook. `SessionStart -> ingest` and
# `PostToolUse -> hook-postedit` are gone: the watcher reindexes on its own,
# so both were doing work the service already does — and doing it *inside*
# the session, which is the blocking behaviour v3 exists to remove.
# `--migrate` removes them from a project that already has them; until then
# both subcommands keep working (`hook-postedit` is a no-op shim).
_HOOK_GROUPS: dict[str, dict] = {
    "UserPromptSubmit": {
        "hooks": [
            {"type": "command",
             "command": f"{_LAUNCHER} hook-inject", "timeout": 30},
        ],
    },
}
# Event -> the mnemo subcommand that identifies "our" hook entry.
_EVENT_SUBCMD = {"UserPromptSubmit": "hook-inject"}
# Hooks v2 generated that v3 removes. `--migrate` deletes exactly these and
# nothing else.
_RETIRED_HOOKS = {"SessionStart": "ingest", "PostToolUse": "hook-postedit"}

# The strict, universal project-memory rule. `.claude/rules/*.md`
# auto-loads into the team lead AND every subagent (subagents do not
# inherit CLAUDE.md), so this is the one place the discipline binds for
# all. This text is the single source — the adopt skill references it
# for conflict resolution rather than duplicating it.
_MEMORY_RULE = """\
# Project memory (mnemo) — binding rule

This project uses **mnemo** for shared, searchable memory. This rule is
mandatory and applies to everyone in the session — the team lead and
every subagent. It replaces any default or built-in memory behavior.

**Location — read carefully.** Memory lives in the **project's own
`.claude/` directory at the repository root** (the project you are
working in). This is NOT `~/.claude/` in your home directory and NOT
any user-level Claude folder. Every path below is relative to the
project root. The curated markdown there is the single source of truth
and the only memory you write to.

## Before non-trivial work

Search the project memory first — the `mnemo` MCP tool `memory_search`.
Narrow with `path_prefix` when you know roughly where to look (for
example `logs` or `topics`); leave it out to search the whole bank. Do
not re-investigate decisions, architecture or pitfalls already recorded.

## After significant work or any decision

Record it in the project tree so it is not lost:

- keep `MEMORY.md` a thin index — links and quick facts only;
- put detail in topic files, one concept per file;
- append day notes under `logs/YYYY-MM-DD.md`.

## Hard constraints

- Edit only the `.md` files in the project's `.claude/`. Never edit the
  index database — it is derived and rebuilt from the `.md`.
- The `.md` in git is the source of truth; the index is disposable.
- Never write shared knowledge to `~/.claude/` or any user-level,
  session-local or built-in memory — only the project's git-tracked
  `.claude/` counts.
- Reindexing is automatic: a background service watches these files and
  re-indexes within seconds of a save. You never run a command for it.
  `memory_reindex` exists only to force the issue.
- A search may answer `status=indexing` (the index is still building —
  retry shortly) or `status=empty` (nothing indexed yet). Neither means
  "no such memory"; only `status=ready` with no hits means that.
- Memory rides with the commit: when a memory `.md` change accompanies
  a code change, `git add` it together and land both in the **same
  commit**; refer to that commit by its **subject/scope, never by a
  hash** (hashes break on force-push/rebase). Never leave memory
  uncommitted trailing a code commit.

## Hygiene

- `MEMORY.md` is an index, not a store — links + quick facts only;
  detail belongs in topic files.
- One concept per topic file; day-by-day notes in `logs/`.
- No duplicates: check what is already recorded before adding.
- No session state ("currently doing X") — only durable knowledge.
- Remove outdated entries: stale memory is worse than none.
- Do not record what the code, git history or CLAUDE.md already says.
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


def _drop_retired_hooks(hooks: dict, path: Path, log: list[str]) -> bool:
    """`--migrate` only: remove the two hooks v3 no longer generates.

    Surgical by construction — it deletes a hook entry only when
    `_is_mnemo_cmd` says mnemo wrote it, and removes the surrounding group
    only once that group is empty. A foreign hook sharing the same event is
    left exactly where it was.
    """
    changed = False
    for event, subcmd in _RETIRED_HOOKS.items():
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
                           f"(mnemo {subcmd}, retired in v3)")
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
    correct. Raises _Refuse on a conflicting mnemo hook."""
    data = _load_json(path)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
    elif not isinstance(hooks, dict):
        raise _Refuse(f"{path}: 'hooks' is not an object; left untouched")

    changed = _drop_retired_hooks(hooks, path, log) if migrate else False
    for event, group in _HOOK_GROUPS.items():
        subcmd = _EVENT_SUBCMD[event]
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


def init_project(root: str | None, *, migrate: bool = False) -> int:
    """Wire mnemo into a project. Returns 0 on success, 1 on refusal
    (in which case NOTHING was written)."""
    paths = resolve(root)
    proj = paths.root
    mcp_path = proj / ".mcp.json"
    settings_path = proj / ".claude" / "settings.json"

    log: list[str] = []
    try:
        new_mcp = _plan_mcp(mcp_path, log, bank_name=bank_name_for(proj),
                            migrate=migrate)
        new_settings = _plan_settings(settings_path, log, migrate)
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
