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
shell form so `~` expands per-user at run time; the MCP `command` is a
`/bin/sh -c` wrapper so `$HOME` expands there too. No machine-specific
path is ever written into git.
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import resolve

# Portable launcher reference. Each teammate's own $HOME resolves at run
# time — nothing machine-specific lands in git.
_LAUNCHER = "~/.claude/mnemo/bin/mnemo"

# `.mcp.json` cannot shell-expand `~`; only documented `${VAR}` works and
# HOME is not guaranteed there. A `/bin/sh -c` wrapper expands $HOME
# reliably and stays portable.
_MCP_SERVER = {
    "command": "/bin/sh",
    "args": ["-c", 'exec "$HOME/.claude/mnemo/bin/mnemo" mcp'],
}

# One hook group per event. Shell form (a bare `command` string, no
# `args`) so the shell expands `~` at run time.
_HOOK_GROUPS: dict[str, dict] = {
    "SessionStart": {
        "hooks": [
            {"type": "command",
             "command": f"{_LAUNCHER} ingest", "timeout": 60},
        ],
    },
    "PostToolUse": {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
            {"type": "command",
             "command": f"{_LAUNCHER} hook-postedit", "timeout": 60},
        ],
    },
    "UserPromptSubmit": {
        "hooks": [
            {"type": "command",
             "command": f"{_LAUNCHER} hook-inject", "timeout": 30},
        ],
    },
}
# Event -> the mnemo subcommand that identifies "our" hook entry.
_EVENT_SUBCMD = {
    "SessionStart": "ingest",
    "PostToolUse": "hook-postedit",
    "UserPromptSubmit": "hook-inject",
}

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

Search the project memory first — the `mnemo` MCP tool `memory_search`
(scope `project`, and your agent scope when relevant). Do not
re-investigate decisions, architecture or pitfalls already recorded.

## After significant work or any decision

Record it in the project tree so it is not lost:

- shared project knowledge -> `<project>/.claude/memory/` — keep
  `MEMORY.md` a thin index, put detail in topic files, append day notes
  under `logs/YYYY-MM-DD.md`;
- agent-specific knowledge -> `<project>/.claude/agent-memory/<role>/`.

## Hard constraints

- Edit only the `.md` files in the project's `.claude/`. Never edit the
  index database — it is derived and rebuilt from the `.md`.
- The `.md` in git is the source of truth; the index is disposable.
- Never write shared knowledge to `~/.claude/` or any user-level,
  session-local or built-in memory — only the project's git-tracked
  `.claude/` counts.
- Reindex is automatic (session start, on memory edit, and relevant
  memory surfaced on each prompt). `memory_reindex` refreshes on demand.

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


def _plan_mcp(path: Path, log: list[str]) -> str | None:
    """Return the new `.mcp.json` text, or None if already correct.
    Raises _Refuse on a conflicting `mnemo` server definition."""
    data = _load_json(path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    elif not isinstance(servers, dict):
        raise _Refuse(f"{path}: 'mcpServers' is not an object; "
                      f"left untouched")

    existing = servers.get("mnemo")
    if existing == _MCP_SERVER:
        log.append(f"  .mcp.json            mnemo server already present")
        return None
    if existing is not None:
        raise _Refuse(
            f"{path}: 'mcpServers.mnemo' exists with a different "
            f"definition.\n      found:    {json.dumps(existing)}\n"
            f"      expected: {json.dumps(_MCP_SERVER)}\n"
            f"      (left untouched — resolve via the adopt skill)")

    # Additive: keep every other server and key, append ours.
    servers["mnemo"] = _MCP_SERVER
    data["mcpServers"] = servers
    log.append(f"  .mcp.json            +mcpServers.mnemo")
    return _dump_json(data)


def _is_mnemo_cmd(command: object, subcmd: str) -> bool:
    """A hook command that targets `mnemo <subcmd>` (any launcher path)."""
    if not isinstance(command, str):
        return False
    toks = command.split()
    return bool(toks) and "mnemo" in command and toks[-1] == subcmd


def _plan_settings(path: Path, log: list[str]) -> str | None:
    """Return the new `.claude/settings.json` text, or None if already
    correct. Raises _Refuse on a conflicting mnemo hook."""
    data = _load_json(path)
    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
    elif not isinstance(hooks, dict):
        raise _Refuse(f"{path}: 'hooks' is not an object; left untouched")

    changed = False
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


def init_project(root: str | None) -> int:
    """Wire mnemo into a project. Returns 0 on success, 1 on refusal
    (in which case NOTHING was written)."""
    paths = resolve(root)
    proj = paths.root
    mcp_path = proj / ".mcp.json"
    settings_path = proj / ".claude" / "settings.json"

    log: list[str] = []
    try:
        new_mcp = _plan_mcp(mcp_path, log)
        new_settings = _plan_settings(settings_path, log)
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

    # Best-effort first ingest: wiring is the deliverable; the model is
    # an explicit separate `warmup` step the adopt skill orchestrates.
    from .embedder import is_model_cached
    from .index import pending_embeddings, reindex

    if pending_embeddings(proj) and not is_model_cached():
        print("  ingest               skipped — model not warmed "
              "(run `mnemo warmup`, then ingest)")
    else:
        reindex(proj)
        print("  ingest               index built")

    print("mnemo init: done. Review the changes, then commit them "
          "(and trust the project in Claude Code).")
    return 0
