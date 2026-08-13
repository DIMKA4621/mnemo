"""Model-independent cross-platform regression checks.

Exercises portable project wiring, bank-root resolution, and the flat
markdown walk without touching the user's real mnemo state.

Two rules for anything added here:

* **Assert the shape, never the source.** Comparing a planned value against
  the constant that produced it is green by construction — it survives any
  change to that constant, including one that breaks the contract.
* Assertions on a shape a later phase introduces go through ``xcheck``, so
  they announce themselves as pending instead of passing vacuously today.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A failure detail can hold Cyrillic (the scaffold fixture is a Ukrainian
# path). On a cp1252 console print() then raises and the suite dies instead
# of reporting — precisely when something has failed.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from src import config, embedder  # noqa: E402
from src.index import scan_bank  # noqa: E402
from src import scaffold  # noqa: E402
from src.scaffold import (  # noqa: E402
    _INSTANCE,
    _LEGACY_INSTANCE,
    _MEMORY_RULE,
    _RULE_ANCESTORS,
    _RULE_CURRENT,
    _RULE_SUPERSEDED,
    _NeedsUntrack,
    _Refuse,
    _git_tracked,
    _plan_mcp,
    _plan_settings,
    _plan_wiring,
    _rule_state,
    _seed_tree,
)

# Stands in for a real bank token: 48 hex, the shape every mnemo credential
# has. Never a live one — these tests write it into files under /tmp.
_FAKE_TOKEN = "a1b2c3d4" * 6

_passed = _failed = _xfailed = _xpassed = 0

# A bank_id is sha1(root)[:16] — correct on exactly one machine with one
# checkout path. It must never reach a git-tracked file.
_BANK_ID_RE = re.compile(r"[0-9a-f]{16}")
# A Windows drive letter, and NOT a URL scheme: `http://` ends in `p:/`,
# which a bare `[A-Za-z]:[\\/]` matches happily. The lookbehind requires the
# letter to stand alone, so `C:\Users` and `"D:/x"` match while `http://`
# and `file://` do not.
_WIN_DRIVE_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")
# Anything long and opaque enough to be a real credential. Documented
# `${VAR}` placeholders stay well under this (MNEMO_API_TOKEN is 15 chars).
_SECRET_RE = re.compile(r"[A-Za-z0-9_\-]{24,}")


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def xcheck(name: str, ok: bool, reason: str, detail: str = "") -> None:
    """An assertion on a shape a LATER phase introduces.

    Failing now is expected and does not fail the suite; passing means the
    phase landed and the assertion has become a real regression guard.
    Never counts as a silent pass — the outcome is always printed.
    """
    global _xfailed, _xpassed
    if ok:
        _xpassed += 1
        print(f"XPASS {name}  ({reason} — now holds; promote to check())")
    else:
        _xfailed += 1
        print(f"xfail {name}  ({reason})  {detail}")


def _read(path: Path) -> str:
    """Raw read, no newline translation — the mirror of `_write` below."""
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path: Path, text: str) -> None:
    """Write exactly these bytes — no platform newline translation.

    `Path.write_text` expands every LF to CRLF on Windows, so a fixture
    written with it does not hold what the test says it holds, and a
    planner output re-written with it comes back with a doubled CR. Every
    fixture and every apply step below goes through this, so the only line
    endings in play are the ones a test asked for.
    """
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _check_mcp_shape(entry: dict, *, placeholders: bool) -> None:
    """Contracts §10.4 — the HTTP wiring, in both forms it is written in.

    ``placeholders`` picks which file this entry belongs in, and the two have
    opposite requirements for the same URL:

    * **template** (``.mcp.json.template``, git-tracked) — every varying value
      is a ``{{VAR}}``, and a literal token there would be the exact failure
      this block exists to prevent;
    * **plain** (``.mcp.json``, git-ignored) — the bank's real token, because
      nothing substitutes it later.

    Both forms carry **no `headers` key at all**. `init` used to duplicate the
    credential into an `Authorization` header for a fallback path nothing
    depends on; the URL is now the only place either value appears.
    """
    check("MCP transport is http", entry.get("type") == "http",
          detail=str(entry.get("type")))
    check("MCP entry carries no headers block", "headers" not in entry,
          detail=str(sorted(entry)))

    url = entry.get("url")
    url = url if isinstance(url, str) else ""
    if placeholders:
        # The host is a variable here too, so there is no literal to test
        # against — the named-placeholder check below is what holds the shape.
        check("template url's host is a placeholder",
              url.startswith("http://{{MNEMO_HOST}}:"), detail=url)
    else:
        check(
            "MCP url is loopback",
            re.fullmatch(r"http://(127\.0\.0\.1|localhost|\[::1\]):\S+", url)
            is not None,
            detail=str(url),
        )

    # **No path segment, in either form.** The token identifies the bank, so a
    # segment would be a second thing saying which bank — free to disagree
    # with the credential, and read as routing by whoever meets it next. This
    # is the assertion that fails if the segment ever creeps back, including
    # as a cosmetic label.
    path = url.partition("://")[2].partition("/")[2].partition("?")[0]
    check("MCP url has no path segment after /mcp", path == "mcp",
          detail=str(path))
    check("MCP url names no bank", "MNEMO_BANK" not in url
          and "bank=" not in url, detail=url)

    if placeholders:
        # Named exactly, not "some {{...}} appears": the variable names are
        # the contract between `.mcp.json.template`, `.mcp.env` and the sed
        # call in `mcp-setup.sh`, and a rename that only one of the two
        # follows produces a URL that is valid and points nowhere.
        for var in ("{{MNEMO_HOST}}", "{{MNEMO_PORT}}", "{{MNEMO_TOKEN}}"):
            check(f"template url carries {var}", var in url, detail=url)
        check(
            "template url holds no literal secret",
            _SECRET_RE.search(url) is None,
            detail=str(_SECRET_RE.findall(url)),
        )
        return

    token = url.partition("?token=")[2]
    check("plain url carries a literal 48-hex bank token",
          re.fullmatch(r"[0-9a-f]{48}", token) is not None, detail=token)


def _check_no_hooks(settings_text: str | None, migrated: dict) -> None:
    """`init` writes no hook, and `--migrate` removes every hook it ever wrote.

    Both halves matter. The first is what keeps a hook out of somebody's
    git-tracked `settings.json`; the second is what actually takes the old
    ones out of projects that already carry them. All four generations are
    named explicitly — a pair dropped from `_RETIRED_HOOKS` must fail here
    rather than vanish along with the loop that iterated it.
    """
    check(
        "a plain init plans no settings change at all",
        settings_text is None,
        detail=str(settings_text),
    )

    retired = [
        ("SessionStart", "ingest"),          # v2: indexed inline, in-session
        ("PostToolUse", "hook-postedit"),    # v2: same
        ("UserPromptSubmit", "hook-inject"), # early v3: injected before the task
        ("SessionStart", "memory-hook"),     # mid v3: injected the index
    ]
    hooks = migrated.get("hooks")
    hooks = hooks if isinstance(hooks, dict) else {}
    for event, subcmd in retired:
        commands = [
            h.get("command")
            for g in hooks.get(event, []) or [] if isinstance(g, dict)
            for h in g.get("hooks", []) or [] if isinstance(h, dict)
        ]
        check(
            f"--migrate unwires {event} -> mnemo {subcmd}",
            not any(
                isinstance(c, str) and "mnemo" in c and c.split()[-1:] == [subcmd]
                for c in commands
            ),
            detail=str(commands),
        )


def test_scaffold() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo platform ") as raw:
        root = Path(raw) / "проєкт з пробілами"
        root.mkdir()
        mcp_path = root / ".mcp.json"
        settings_path = root / ".claude" / "settings.json"
        settings_path.parent.mkdir()

        mcp_path.write_text(
            json.dumps({"mcpServers": {"foreign": {"command": "other"}}}),
            encoding="utf-8",
        )
        settings_path.write_text(
            json.dumps({"permissions": {"allow": ["Read"]}}),
            encoding="utf-8",
        )

        mcp_text = _plan_mcp(mcp_path, [], token=_FAKE_TOKEN)
        # `init` wires NO hook and has no flag that makes one (design #27):
        # nothing to plan, so the file is not even rewritten. Asserted before
        # anything else, because a regression here puts a hook into somebody's
        # git-tracked settings.
        default_settings_text = _plan_settings(settings_path, [])
        mcp = json.loads(mcp_text or "{}")

        entry = mcp.get("mcpServers", {}).get(_INSTANCE)
        check("mnemo MCP server written", isinstance(entry, dict), detail=str(mcp))
        entry_text = json.dumps(entry, ensure_ascii=False)

        # `.mcp.json` is git-ignored and carries a real token, so the
        # no-secrets rule does NOT apply to it — but the no-machine-specifics
        # rule still does. A path that only exists on this laptop is useless
        # to the person who clones, token or no token.
        home = Path.home()
        check(
            "MCP entry carries no machine-specific path",
            str(home) not in entry_text
            and home.as_posix() not in entry_text
            and _WIN_DRIVE_RE.search(entry_text) is None,
            detail=entry_text,
        )
        # "no bank_id anywhere in the entry" cannot be asserted any more: a
        # 48-hex token contains 16-hex substrings by construction, so the
        # blanket scan would fail on every correct entry. The precise form of
        # the same rule — the bank *segment* is not a bank_id — is inside
        # `_check_mcp_shape`, and that is the one that was ever meaningful.
        _check_mcp_shape(entry if isinstance(entry, dict) else {},
                         placeholders=False)

        check(
            "foreign MCP server preserved",
            "foreign" in mcp.get("mcpServers", {}),
            detail=str(mcp),
        )
        # Every hook mnemo ever wrote, wired the way a real project would
        # carry it — so `--migrate` is asked to remove something that is
        # actually there, not to no-op past an empty file.
        settings_path.write_text(json.dumps({
            "permissions": {"allow": ["Read"]},
            "hooks": {
                "SessionStart": [
                    {"hooks": [
                        {"type": "command", "command": "~/.claude/mnemo/bin/mnemo ingest"},
                        {"type": "command", "command": "~/.claude/mnemo/bin/mnemo memory-hook"},
                        {"type": "command", "command": "/usr/bin/somebody-elses-hook"},
                    ]},
                ],
                "PostToolUse": [
                    {"hooks": [{"type": "command",
                                "command": "~/.claude/mnemo/bin/mnemo hook-postedit"}]},
                ],
                "UserPromptSubmit": [
                    {"hooks": [{"type": "command",
                                "command": "~/.claude/mnemo/bin/mnemo hook-inject"}]},
                ],
            },
        }), encoding="utf-8")
        migrated_text = _plan_settings(settings_path, [], True)
        migrated = json.loads(migrated_text or "{}")
        _check_no_hooks(default_settings_text, migrated)
        check(
            "foreign settings preserved through --migrate",
            migrated.get("permissions") == {"allow": ["Read"]},
            detail=str(migrated),
        )
        # Surgical, not a broom: a hook mnemo did not author shares the
        # SessionStart event and must come through untouched.
        foreign = [
            h.get("command")
            for g in migrated.get("hooks", {}).get("SessionStart", []) or []
            for h in g.get("hooks", []) or []
        ]
        check(
            "a foreign hook sharing the event survives",
            foreign == ["/usr/bin/somebody-elses-hook"],
            detail=str(foreign),
        )
        settings_path.write_text(migrated_text or "", encoding="utf-8")
        check(
            "--migrate is idempotent once the hooks are gone",
            _plan_settings(settings_path, [], True) is None,
        )

        mcp_path.write_text(mcp_text or "", encoding="utf-8")
        check("MCP idempotent",
              _plan_mcp(mcp_path, [], token=_FAKE_TOKEN) is None)
        # A rotated token is not a conflict — it is the same entry with a new
        # credential, and `init` must re-issue it rather than refuse or, worse,
        # report "already present" and leave wiring that no longer opens.
        rotated = _plan_mcp(mcp_path, [], token="b" * 48)
        check(
            "a rotated bank token is rewritten, not refused",
            rotated is not None
            and "b" * 48 in json.loads(rotated)["mcpServers"][_INSTANCE]["url"],
            detail=str(rotated),
        )
        # A plain re-run never touches hooks — not to add one, and not to
        # remove one behind the user's back either. Removal is `--migrate`,
        # and only that.
        settings_path.write_text(json.dumps({
            "hooks": {"UserPromptSubmit": [
                {"hooks": [{"type": "command",
                            "command": "~/.claude/mnemo/bin/mnemo hook-inject"}]},
            ]},
        }), encoding="utf-8")
        check(
            "a plain re-run leaves an existing hook alone",
            _plan_settings(settings_path, []) is None
            and "hook-inject" in settings_path.read_text(encoding="utf-8"),
        )

        # Two legacy generations exist in the wild (contracts §15.2), and
        # `mnemo init --migrate` must recognise BOTH in phase 4. Projects
        # adopted before the windows branch carry L1, after it L2.
        l1 = {
            "command": "/bin/sh",
            "args": ["-c", 'exec "$HOME/.claude/mnemo/bin/mnemo" mcp'],
        }
        l2 = {
            "type": "stdio",
            "command": "${HOME}/.claude/mnemo/bin/mnemo",
            "args": ["mcp"],
        }

        def refuses(server: dict) -> bool:
            mcp_path.write_text(
                json.dumps({"mcpServers": {"mnemo": server}}), encoding="utf-8"
            )
            try:
                _plan_mcp(mcp_path, [], token=_FAKE_TOKEN)
            except _Refuse:
                return True
            return False

        def migrates(server: dict) -> dict:
            mcp_path.write_text(
                json.dumps({"mcpServers": {"mnemo": server}}), encoding="utf-8"
            )
            text = _plan_mcp(mcp_path, [], token=_FAKE_TOKEN,
                             migrate=True)
            return json.loads(text or "{}").get("mcpServers", {}).get(_INSTANCE, {})

        check("legacy L1 (/bin/sh wrapper) is an explicit conflict", refuses(l1))
        # L2 is still the LIVE value today, so plain `init` correctly treats
        # it as already-wired. It becomes a conflict only once phase 4 makes
        # the HTTP form canonical.
        check(
            "legacy L2 (stdio ${HOME} launcher) is an explicit conflict",
            refuses(l2),
        )
        # Both shapes call `mnemo mcp`, a subcommand that no longer exists, so
        # a project carrying either has *dead* wiring — `--migrate` is what
        # brings it back to life, and it is the only thing that may.
        for name, server in (("L1", l1), ("L2", l2)):
            migrated = migrates(server)
            check(f"--migrate rewrites legacy {name} to the http form",
                  migrated.get("type") == "http" and "args" not in migrated,
                  detail=str(migrated))


_SETUP_SH = """\
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/.mcp.json.template"
source "$SCRIPT_DIR/.mcp.env"

sed \\
  -e "s|{{CHROME_PORT}}|${CHROME_PORT}|g" \\
  "$TEMPLATE" > "$SCRIPT_DIR/.mcp.json"

echo "✓ .mcp.json"
"""


def test_scaffold_template_project() -> None:
    """A project on the `project-mcp-setup` convention (contracts §10.4).

    The hazard being guarded is silent: `mcp-setup.sh` regenerates `.mcp.json`
    wholesale, so an entry written straight into that file survives exactly
    until the next run and then disappears with no error. The entry has to
    land in the template, and every varying value has to reach `.mcp.env` and
    the sed call, or the generated `.mcp.json` carries an unsubstituted
    `{{MNEMO_TOKEN}}` and simply does not open.
    """
    with tempfile.TemporaryDirectory(prefix="mnemo template ") as raw:
        proj = Path(raw) / "проєкт"
        proj.mkdir()
        template = proj / ".mcp.json.template"
        env = proj / ".mcp.env"
        example = proj / ".mcp.env.example"
        setup = proj / "mcp-setup.sh"
        mcp_json = proj / ".mcp.json"

        _write(
            template,
            json.dumps({"mcpServers": {"chrome": {
                "command": "npx",
                "args": ["-y", "x", "--url", "http://127.0.0.1:{{CHROME_PORT}}"],
            }}}, indent=2),
        )
        _write(example, "# chrome\nCHROME_PORT=9902\n")
        # CRLF on purpose, and only here: `.mcp.env` is routinely edited on
        # Windows, and an edit that silently converts a whole file to LF is a
        # whole-file diff in someone else's repository.
        _write(env, "# chrome\r\nCHROME_PORT=9902\r\n")
        _write(setup, _SETUP_SH)
        stale = json.dumps({"mcpServers": {"chrome": {"command": "npx"}}})
        _write(mcp_json, stale)

        wiring = _plan_wiring(proj, token=_FAKE_TOKEN, migrate=False)
        written = {path.name: text for path, text in wiring.writes}

        check(".mcp.json is NOT written when a template exists",
              ".mcp.json" not in written, detail=str(sorted(written)))

        entry = (json.loads(written.get(".mcp.json.template", "{}"))
                 .get("mcpServers", {}))
        check("mnemo entry lands in the template", _INSTANCE in entry,
              detail=str(sorted(entry)))
        check("foreign template entry preserved", "chrome" in entry,
              detail=str(sorted(entry)))
        _check_mcp_shape(entry.get(_INSTANCE, {}), placeholders=True)

        env_text = written.get(".mcp.env", "")
        check("real values land in .mcp.env",
              f"MNEMO_TOKEN={_FAKE_TOKEN}" in env_text
              and "MNEMO_PORT=8918" in env_text,
              detail=env_text)
        check("no bank name reaches .mcp.env at all",
              "MNEMO_BANK" not in env_text, detail=env_text)
        check("foreign .mcp.env variables preserved",
              "CHROME_PORT=9902\r\n" in env_text, detail=repr(env_text))
        # A CRLF file stays CRLF and an LF file stays LF. Without this the
        # edit is correct and the diff is the whole file — and `mcp-setup.sh`,
        # written the same way, would gain a `\r` on every line and stop being
        # a runnable bash script on Linux.
        check("an existing CRLF file keeps CRLF",
              "\n" not in env_text.replace("\r\n", ""), detail=repr(env_text))
        check("an existing LF file keeps LF",
              "\r" not in written.get("mcp-setup.sh", ""),
              detail=repr(written.get("mcp-setup.sh", "")[:80]))

        example_text = written.get(".mcp.env.example", "")
        check(".mcp.env.example gains a blank mnemo token",
              "MNEMO_TOKEN=\n" in example_text, detail=example_text)
        check(".mcp.env.example holds no literal secret",
              _FAKE_TOKEN not in example_text, detail=example_text)

        setup_text = written.get("mcp-setup.sh", "")
        check("sed lines land inside the existing sed invocation",
              all(f'{{{{MNEMO_{v}}}}}' in setup_text
                  for v in ("PORT", "TOKEN"))
              and setup_text.index('{{MNEMO_TOKEN}}')
              < setup_text.index('"$TEMPLATE"'),
              detail=setup_text)
        check("no MNEMO_BANK substitution is written",
              "{{MNEMO_BANK}}" not in setup_text, detail=setup_text)
        check("the foreign sed line is kept",
              '{{CHROME_PORT}}' in setup_text, detail=setup_text)

        # Apply, then re-plan: a second `init` on an already-wired project
        # must be a no-op, or every run would churn four git-tracked files.
        for path, text in wiring.writes:
            _write(path, text)
        again = _plan_wiring(proj, token=_FAKE_TOKEN, migrate=False)
        check("template wiring is idempotent", not again.writes,
              detail=str([p.name for p, _ in again.writes]))

        # After a token rotation the variable must be REPLACED where it
        # already sits, not appended a second time. A duplicate would win
        # under `source` and the file would work while saying two different
        # things — the shape of bug nobody finds.
        rotated = "f" * 48
        turned = {p.name: t for p, t in _plan_wiring(
            proj, token=rotated,
            migrate=False).writes}
        env_text = turned.get(".mcp.env", "")
        check("a rotated token replaces the .mcp.env line in place",
              env_text.count("MNEMO_TOKEN=") == 1
              and f"MNEMO_TOKEN={rotated}" in env_text,
              detail=env_text)
        check("rotation touches .mcp.env and nothing else",
              sorted(turned) == [".mcp.env"], detail=str(sorted(turned)))

        # The dead stdio shape lives in templates too — that is where
        # voice-agent carries it — and `--migrate` has to reach it there.
        doc = json.loads(template.read_text(encoding="utf-8"))
        doc["mcpServers"]["mnemo"] = {
            "command": "/bin/sh",
            "args": ["-c", 'exec "$HOME/.claude/mnemo/bin/mnemo" mcp'],
        }
        _write(template, json.dumps(doc, indent=2))
        try:
            _plan_wiring(proj, token=_FAKE_TOKEN,
                         migrate=False)
            refused = False
        except _Refuse:
            refused = True
        check("a legacy entry inside the template is a conflict", refused)

        fixed = _plan_wiring(proj, token=_FAKE_TOKEN, migrate=True)
        migrated = {p.name: t for p, t in fixed.writes}
        entry = (json.loads(migrated.get(".mcp.json.template", "{}"))
                 .get("mcpServers", {}).get(_INSTANCE, {}))
        check("--migrate fixes the legacy entry inside the template",
              entry.get("type") == "http" and "{{MNEMO_TOKEN}}" in entry.get("url", ""),
              detail=str(entry))


def test_scaffold_drops_the_bank_segment() -> None:
    """The third generation `--migrate` has to fix: `/mcp/<bank>?token=…`.

    A project wired under the previous shape carries a *valid* token and a URL
    the backend now rejects with a 400. Unlike the two stdio legacies this is
    not a refusal — it is the current generation with a stale address, so a
    plain `init` rewrites it. Alongside it, three things mnemo itself wrote
    for that shape are now orphaned and must be pruned, not left: the
    `{{MNEMO_BANK}}` placeholder is gone from the template, so a `MNEMO_BANK`
    in `.mcp.env` and a `sed -e` line for it substitute nothing while looking
    authoritative.
    """
    with tempfile.TemporaryDirectory(prefix="mnemo segment ") as raw:
        proj = Path(raw) / "prev-gen"
        proj.mkdir()

        # --- plain project on the old shape -----------------------------
        mcp = proj / ".mcp.json"
        _write(mcp, json.dumps({"mcpServers": {"mnemo": {
            "type": "http",
            "url": f"http://127.0.0.1:8918/mcp/some%20bank?token={_FAKE_TOKEN}",
            "headers": {"Authorization": f"Bearer {_FAKE_TOKEN}",
                        "X-Mnemo-Bank": "some bank"},
        }}}, indent=2))
        text = _plan_mcp(mcp, [], token=_FAKE_TOKEN)
        entry = json.loads(text or "{}").get("mcpServers", {}).get(_INSTANCE, {})
        check("a stale /mcp/<bank> entry is rewritten without --migrate",
              text is not None, detail=str(text))
        _check_mcp_shape(entry, placeholders=False)
        check("the stale headers block goes with it", "headers" not in entry,
              detail=str(sorted(entry)))

        # --- template project on the old shape --------------------------
        proj2 = Path(raw) / "prev-gen-template"
        proj2.mkdir()
        _write(proj2 / ".mcp.json.template", json.dumps({"mcpServers": {
            "mnemo": {
                "type": "http",
                "url": "http://127.0.0.1:{{MNEMO_PORT}}/mcp/{{MNEMO_BANK}}"
                       "?token={{MNEMO_TOKEN}}",
            }}}, indent=2))
        _write(proj2 / ".mcp.env",
               "# mnemo\nMNEMO_PORT=8918\nMNEMO_BANK=some%20bank\n"
               f"MNEMO_TOKEN={_FAKE_TOKEN}\n")
        _write(proj2 / ".mcp.env.example",
               "# mnemo\nMNEMO_PORT=8918\nMNEMO_BANK=\nMNEMO_TOKEN=\n")
        _write(proj2 / "mcp-setup.sh", _SETUP_SH.replace(
            '  "$TEMPLATE"',
            '  -e "s|{{MNEMO_PORT}}|${MNEMO_PORT}|g" \\\n'
            '  -e "s|{{MNEMO_BANK}}|${MNEMO_BANK}|g" \\\n'
            '  -e "s|{{MNEMO_TOKEN}}|${MNEMO_TOKEN}|g" \\\n'
            '  "$TEMPLATE"'))

        # A plain `init` updates the URL but DELETES NOTHING. Being additive
        # is the property this command is trusted for, and "it only removed
        # its own key" is not a distinction worth spending it on.
        plain = {p.name: t for p, t in
                 _plan_wiring(proj2, token=_FAKE_TOKEN, migrate=False).writes}
        entry = (json.loads(plain.get(".mcp.json.template", "{}"))
                 .get("mcpServers", {}).get(_INSTANCE, {}))
        _check_mcp_shape(entry, placeholders=True)
        # It adds what the rewritten URL now needs, and only that. `MNEMO_HOST`
        # is not optional here: the URL this same run plans carries
        # `{{MNEMO_HOST}}`, and a placeholder with no variable behind it and no
        # `sed -e` line to expand it passes through into the generated
        # `.mcp.json` verbatim while `mcp-setup.sh` still exits 0 — the silent
        # half-write this whole layer exists to prevent.
        check("a plain init adds MNEMO_HOST to .mcp.env",
              "MNEMO_HOST=" in plain.get(".mcp.env", ""),
              detail=plain.get(".mcp.env", ""))
        check("a plain init adds MNEMO_HOST to .mcp.env.example",
              "MNEMO_HOST=" in plain.get(".mcp.env.example", ""),
              detail=plain.get(".mcp.env.example", ""))
        check("a plain init adds the MNEMO_HOST sed line",
              "{{MNEMO_HOST}}" in plain.get("mcp-setup.sh", ""),
              detail=plain.get("mcp-setup.sh", ""))
        # Adding is the whole of it: the retired variable is still there,
        # because a plain run deletes nothing. That is the property being
        # guarded, and it did not change when a variable was added.
        check("MNEMO_BANK survives a plain init",
              "MNEMO_BANK" in plain.get(".mcp.env", _read(proj2 / ".mcp.env"))
              and "{{MNEMO_BANK}}" in plain.get(
                  "mcp-setup.sh", _read(proj2 / "mcp-setup.sh")))

        # `--migrate` is what prunes.
        written = {p.name: t for p, t in
                   _plan_wiring(proj2, token=_FAKE_TOKEN,
                                migrate=True).writes}
        check("--migrate prunes MNEMO_BANK from .mcp.env",
              "MNEMO_BANK" not in written.get(".mcp.env", ""),
              detail=written.get(".mcp.env", ""))
        check("--migrate prunes MNEMO_BANK from .mcp.env.example",
              "MNEMO_BANK" not in written.get(".mcp.env.example", ""),
              detail=written.get(".mcp.env.example", ""))
        setup_text = written.get("mcp-setup.sh", "")
        check("--migrate prunes the MNEMO_BANK sed line",
              "{{MNEMO_BANK}}" not in setup_text, detail=setup_text)
        # `mcp-setup.sh` is the USER's file — mnemo only ever appended to it.
        # Pruning our line must not disturb one other character of it.
        check("the foreign sed line survives the prune",
              "{{CHROME_PORT}}" in setup_text, detail=setup_text)
        check("mnemo's remaining sed lines survive the prune",
              "{{MNEMO_PORT}}" in setup_text
              and "{{MNEMO_TOKEN}}" in setup_text, detail=setup_text)
        before = _SETUP_SH.splitlines()
        after = [l for l in setup_text.splitlines() if "MNEMO" not in l]
        check("no other line of mcp-setup.sh is reflowed or reordered",
              after == before, detail=str([l for l in after if l not in before]))

        # And it settles: applying the plan makes the next run a no-op.
        for path, text in _plan_wiring(proj2, token=_FAKE_TOKEN,
                                       migrate=True).writes:
            _write(path, text)
        again = _plan_wiring(proj2, token=_FAKE_TOKEN, migrate=True)
        check("the migrated template project is then idempotent",
              not again.writes, detail=str([p.name for p, _ in again.writes]))


def test_setup_script_refresh() -> None:
    """An older revision of mnemo's own script is replaced; an edit is not.

    The marker says which KIND of file this is, never which REVISION — so
    before this, `init` looked at a script it had written a year ago, said
    "mine, nothing to add", and left a known-broken file in place forever.
    That is exactly how the bash 3.2 fix would have failed to reach any
    project already adopted.
    """
    # Imported under another name deliberately: the module-level `_SETUP_SH`
    # in this file is the *user skill's* sed-based script, and confusing the
    # two would test the wrong file entirely.
    import hashlib

    from src.scaffold import _SETUP_SH as MNEMO_SH
    from src.scaffold import _SETUP_SUPERSEDED, _setup_state

    print("\n=== setup script refresh ===")

    def plan(text: str | None, name: str = "mcp-setup.sh",
             *, adopted: bool = False) -> tuple:
        """`adopted` = the project already has a template layer.

        Which is the case that matters and the one this test first missed:
        the refresh initially lived in the seed-the-layer branch, so it ran
        only where no old script could exist. Every stale-script assertion
        below is made in BOTH shapes for that reason.
        """
        with tempfile.TemporaryDirectory(prefix="mnemo refresh ") as raw:
            proj = Path(raw) / "proj"
            (proj / ".claude").mkdir(parents=True)
            if text is not None:
                _write(proj / name, text)
            if adopted:
                _write(proj / ".mcp.json.template", json.dumps(
                    {"mcpServers": {"foreign": {"command": "npx"}}}, indent=2))
                _write(proj / ".mcp.env", "FOREIGN=1\n")
            wiring = _plan_wiring(proj, token=_FAKE_TOKEN, migrate=False)
            written = {p.name: t for p, t in wiring.writes}
            return written, wiring.notes, wiring.log

    # The digests are the mechanism; a typo in one silently removes a whole
    # generation of projects from updates, exactly as it would for the rule.
    check("every superseded digest is a sha256",
          all(len(d) == 64 and not set(d) - set("0123456789abcdef")
              for d in _SETUP_SUPERSEDED),
          detail=str(sorted(_SETUP_SUPERSEDED)[:1]))
    check("the current text is not among them",
          hashlib.sha256(MNEMO_SH.encode("utf-8")).hexdigest()
          not in _SETUP_SUPERSEDED)

    with tempfile.TemporaryDirectory(prefix="mnemo state ") as raw:
        probe = Path(raw) / "mcp-setup.sh"
        check("an absent script reads as absent",
              _setup_state(probe, MNEMO_SH) == "absent")
        _write(probe, MNEMO_SH)
        check("our current text reads as current",
              _setup_state(probe, MNEMO_SH) == "current")
        # A checkout on Windows can hand back CRLF. Rewriting the file to
        # change nothing but line endings is a whole-file diff for nothing.
        _write(probe, MNEMO_SH.replace("\n", "\r\n"))
        check("a CRLF copy of it still reads as current",
              _setup_state(probe, MNEMO_SH) == "current")
        _write(probe, MNEMO_SH + "\n# hand edit\n")
        check("our marker with foreign bytes reads as edited",
              _setup_state(probe, MNEMO_SH) == "edited")
        _write(probe, "#!/bin/sh\nsed -e 's|{{X}}|1|g' t > o\n")
        check("somebody else's script reads as foreign",
              _setup_state(probe, MNEMO_SH) == "foreign")

    written, _, log = plan(None)
    check("a project with no script gets one",
          written.get("mcp-setup.sh") == MNEMO_SH)
    check("and it is announced as created",
          any("created" in line and "mcp-setup.sh" in line for line in log),
          detail=str([l for l in log if "mcp-setup" in l]))

    written, notes, log = plan(MNEMO_SH)
    check("the current script is left alone",
          "mcp-setup.sh" not in written, detail=str(sorted(written)))
    check("and says nothing about it", not any("mcp-setup.sh" in n
                                               for n in notes))

    # The real case: v1 of the dynamic script, the one broken on bash 3.2.
    old = _read(Path(__file__).parent / "fixtures" / "mcp-setup-v1.sh")
    check("the v1 fixture is a recognised revision",
          hashlib.sha256(old.encode("utf-8")).hexdigest() in _SETUP_SUPERSEDED,
          detail="fixture drifted from the digest list")
    written, notes, log = plan(old)
    check("an older revision is refreshed",
          written.get("mcp-setup.sh") == MNEMO_SH,
          detail=str(sorted(written)))
    check("and the refresh is announced, not silent",
          any("refreshed" in line and "mcp-setup.sh" in line for line in log),
          detail=str([l for l in log if "mcp-setup" in l]))

    # The case the whole feature exists for, and the one the first version of
    # this test could not have caught: a project that was adopted long ago.
    written, notes, log = plan(old, adopted=True)
    check("an older revision is refreshed in an ALREADY-adopted project",
          written.get("mcp-setup.sh") == MNEMO_SH,
          detail=str(sorted(written)))
    check("the foreign template entry survives the refresh",
          "foreign" in json.loads(
              written.get(".mcp.json.template", "{}")).get("mcpServers", {}),
          detail=written.get(".mcp.json.template", "")[:120])

    written, notes, log = plan(MNEMO_SH, adopted=True)
    check("and an already-current one is still left alone",
          "mcp-setup.sh" not in written, detail=str(sorted(written)))

    for shape in (False, True):
        written, notes, _ = plan(old + "\n# my own line\n", adopted=shape)
        where = "adopted" if shape else "fresh"
        check(f"an edited copy is NOT overwritten ({where})",
              "mcp-setup.sh" not in written, detail=str(sorted(written)))
        check(f"and the user is told why ({where})",
              any("mcp-setup.sh" in n and "left untouched" in n for n in notes),
              detail=str(notes))


def test_scaffold_hand_edited_sed_line() -> None:
    """A retired sed line mnemo did NOT write is left alone, and reported.

    `mcp-setup.sh` belongs to the user's `project-mcp-setup` skill; mnemo only
    ever appended lines to it. So removal matches the exact line mnemo would
    have written — never "any line mentioning our placeholder". A line someone
    has since edited is their intent, and guessing at it is how a project ends
    up unable to regenerate its own `.mcp.json`.
    """
    with tempfile.TemporaryDirectory(prefix="mnemo handedit ") as raw:
        proj = Path(raw) / "edited"
        proj.mkdir()
        _write(proj / ".mcp.json.template", json.dumps({"mcpServers": {
            "mnemo": {"type": "http",
                      "url": "http://127.0.0.1:{{MNEMO_PORT}}/mcp"
                             "?token={{MNEMO_TOKEN}}"}}}, indent=2))
        _write(proj / ".mcp.env",
               f"MNEMO_PORT=8918\nMNEMO_TOKEN={_FAKE_TOKEN}\n")
        # Same placeholder, different substitution — someone routed it through
        # another variable. Not a line mnemo ever wrote.
        hand = '  -e "s|{{MNEMO_BANK}}|${MY_OWN_BANK_VAR}|g" \\'
        _write(proj / "mcp-setup.sh", _SETUP_SH.replace(
            '  "$TEMPLATE"',
            '  -e "s|{{MNEMO_PORT}}|${MNEMO_PORT}|g" \\\n'
            + hand + '\n'
            '  -e "s|{{MNEMO_TOKEN}}|${MNEMO_TOKEN}|g" \\\n'
            '  "$TEMPLATE"'))

        wiring = _plan_wiring(proj, token=_FAKE_TOKEN, migrate=True)
        written = {p.name: t for p, t in wiring.writes}
        setup_text = written.get("mcp-setup.sh", _read(proj / "mcp-setup.sh"))
        check("a hand-edited retired sed line is NOT removed",
              hand in setup_text, detail=setup_text)
        check("and it is reported rather than silently kept",
              any("did not write" in n for n in wiring.notes),
              detail=str(wiring.notes))


def test_scaffold_renames_the_legacy_key() -> None:
    """The `mcpServers` key moved: `mnemo` -> `mnemo-memory`.

    A project wired before the rename carries the old key. Writing the new one
    beside it would leave two entries authenticating into the SAME bank — two
    connections, duplicate tools, no hint which is which — so the old key is
    renamed rather than joined. The line for whether that needs `--migrate` is
    the one already in force: mnemo's own HTTP entry is unambiguous and moves
    on a plain run, an stdio generation still waits to be asked.

    And the boundary that matters most: a server somebody ELSE called `mnemo`
    is not mnemo's to rename, delete, or read as legacy.
    """
    with tempfile.TemporaryDirectory(prefix="mnemo rename ") as raw:
        current = {
            "type": "http",
            "url": f"http://127.0.0.1:8918/mcp?token={_FAKE_TOKEN}",
        }
        stdio = {
            "type": "stdio",
            "command": "${HOME}/.claude/mnemo/bin/mnemo",
            "args": ["mcp"],
        }

        def plan(doc: dict, *, migrate: bool = False):
            path = Path(raw) / f"mcp-{abs(hash(json.dumps(doc, sort_keys=True)))}.json"
            _write(path, json.dumps(doc, indent=2))
            text = _plan_mcp(path, [], token=_FAKE_TOKEN, migrate=migrate)
            return json.loads(text or "{}").get("mcpServers", {})

        servers = plan({"mcpServers": {_LEGACY_INSTANCE: current,
                                       "foreign": {"command": "other"}}})
        check("a plain init renames the legacy key",
              _INSTANCE in servers and _LEGACY_INSTANCE not in servers,
              detail=str(sorted(servers)))
        check("the rename keeps a foreign server untouched",
              servers.get("foreign") == {"command": "other"},
              detail=str(servers.get("foreign")))

        try:
            plan({"mcpServers": {_LEGACY_INSTANCE: stdio}})
            refused = False
        except _Refuse:
            refused = True
        check("an stdio entry under the old key still needs --migrate", refused)

        servers = plan({"mcpServers": {_LEGACY_INSTANCE: stdio}}, migrate=True)
        check("--migrate rewrites the shape AND moves the key",
              servers.get(_INSTANCE) == current
              and _LEGACY_INSTANCE not in servers,
              detail=str(sorted(servers)))

        # Both keys present: the new one is already correct, but leaving the
        # old one would be exactly the duplicate this rename exists to avoid,
        # so the plan is a write even though the target entry does not change.
        servers = plan({"mcpServers": {_LEGACY_INSTANCE: current,
                                       _INSTANCE: current}})
        check("a duplicate old key is dropped even when the new one is right",
              servers.get(_INSTANCE) == current
              and _LEGACY_INSTANCE not in servers,
              detail=str(sorted(servers)))

        # Somebody else's server that happens to be called `mnemo`. Not ours
        # to touch -- mnemo adds its own key beside it and leaves it alone.
        stranger = {"type": "stdio", "command": "mnemo-cli", "args": ["serve"]}
        servers = plan({"mcpServers": {_LEGACY_INSTANCE: stranger}})
        check("a foreign server named 'mnemo' is never renamed or removed",
              servers.get(_LEGACY_INSTANCE) == stranger
              and servers.get(_INSTANCE) == current,
              detail=str(servers))


def test_memory_rule_refresh() -> None:
    """`init` refreshes its own rule text, and only its own.

    The rule is the one seeded file mnemo may rewrite: it is mnemo's own
    instruction text and it has grown, so a project adopted months ago would
    otherwise keep months-old rules with nothing saying so. The boundary is
    the digest — bytes that hash to a redaction mnemo wrote are replaceable,
    anything else is somebody's edit and outranks the update.
    """
    print("\n=== memory rule refresh ===")

    def seed(existing: bytes | None) -> tuple[str, bytes]:
        """Run `_seed_tree` over a project whose rule starts as `existing`."""
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp) / "proj" / ".claude"
            rule = claude / "rules" / "mnemo-memory.md"
            if existing is not None:
                rule.parent.mkdir(parents=True)
                rule.write_bytes(existing)
            log: list[str] = []
            _seed_tree(claude, log)
            line = next((ln for ln in log if "mnemo-memory.md" in ln), "")
            return line, rule.read_bytes()

    current = _MEMORY_RULE.encode("utf-8")

    line, out = seed(None)
    check("absent  -> created", "created" in line)
    check("absent  -> exact current bytes", out == current)

    line, out = seed(current)
    check("current -> kept", "already current" in line)
    check("current -> byte-identical", out == current)

    # The current text with CRLF is an ancestor by construction: that is how
    # every pre-`_write` Windows adoption landed. It is the one stale case
    # this test can build without reaching into git.
    line, out = seed(_MEMORY_RULE.replace("\n", "\r\n").encode("utf-8"))
    check("CRLF    -> updated", "updated" in line)
    check("CRLF    -> normalised to LF", out == current)
    check("CRLF    -> no CR survives", b"\r" not in out)

    edited = current + b"\n<!-- project-specific addition -->\n"
    line, out = seed(edited)
    check("edited  -> left alone", "edited here" in line)
    check("edited  -> bytes untouched", out == edited)

    line, out = seed(b"something else entirely\n")
    check("foreign -> left alone", "edited here" in line)
    check("foreign -> bytes untouched", out == b"something else entirely\n")

    # A curated file must never be swept up by this. `MEMORY.md` carries the
    # user's own content and is only ever created when missing.
    with tempfile.TemporaryDirectory() as tmp:
        claude = Path(tmp) / "proj" / ".claude"
        index = claude / "memory" / "MEMORY.md"
        index.parent.mkdir(parents=True)
        index.write_text("# my notes\n", encoding="utf-8")
        _seed_tree(claude, [])
        check("MEMORY.md is never rewritten",
              index.read_text(encoding="utf-8") == "# my notes\n")

    # Structural guards on the digest list itself. A duplicate or a pasted
    # current hash would not fail anything visibly: the rule would simply be
    # rewritten with identical bytes on every run, forever.
    check("superseded digests are distinct",
          len(set(_RULE_SUPERSEDED)) == len(_RULE_SUPERSEDED),
          f"{len(_RULE_SUPERSEDED)} listed, {len(set(_RULE_SUPERSEDED))} unique")
    check("current is not listed as superseded",
          _RULE_CURRENT not in _RULE_SUPERSEDED)
    check("ancestors = superseded + current-CRLF",
          len(_RULE_ANCESTORS) == len(_RULE_SUPERSEDED) + 1)

    # Bind the hardcoded digests to the history they claim to describe. This
    # is what makes the list verifiable rather than a set of magic numbers —
    # but it needs git and this checkout, so a missing commit is reported and
    # skipped rather than failed.
    import ast
    import subprocess

    commits = ("8312de3", "5d5fab3", "4b80845", "307c5bf", "895208b")
    matched, missing = 0, []
    for commit in commits:
        try:
            blob = subprocess.run(
                ["git", "show", f"{commit}:src/scaffold.py"],
                capture_output=True, check=True,
                cwd=Path(__file__).resolve().parent.parent,
            ).stdout.decode("utf-8")
        except (OSError, subprocess.CalledProcessError):
            missing.append(commit)
            continue
        text = None
        for node in ast.parse(blob).body:
            if (isinstance(node, ast.Assign)
                    and any(getattr(t, "id", None) == "_MEMORY_RULE"
                            for t in node.targets)
                    and isinstance(node.value, ast.Constant)):
                text = node.value.value
        if text is None:
            missing.append(commit)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            for ending in ("\n", "\r\n"):
                path = Path(tmp) / "rule.md"
                path.write_bytes(text.replace("\n", ending).encode("utf-8"))
                if _rule_state(path) == "stale":
                    matched += 1
    if missing:
        print(f"note  history check skipped for {', '.join(missing)} "
              f"(commit or constant not reachable from this checkout)")
    expected = 2 * (len(commits) - len(missing))
    if expected:
        check("every reachable past redaction classifies as stale",
              matched == expected, f"{matched}/{expected}")


def test_adopted_project_discovery() -> None:
    """Finding the projects a v2→v3 upgrade leaves stranded.

    v2 kept no registry, so nothing records which projects used mnemo, and
    the indexes cannot be asked: a v2 database has no `meta` table and its
    filename is `sha1(root)`, which does not invert. Claude Code's own
    `~/.claude.json` is the only source that holds absolute project paths.
    """
    print("\n=== adopted project discovery ===")

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        home.mkdir()

        live = Path(tmp) / "live"
        (live / ".claude").mkdir(parents=True)
        gone = Path(tmp) / "gone"          # recorded, then deleted or unplugged

        (home / ".claude.json").write_text(json.dumps({
            "projects": {
                str(live): {},
                str(live).replace("\\", "/"): {},   # same tree, other separator
                str(gone): {},
                "relative/path": {},
                "": {},
            }
        }), encoding="utf-8")

        with patch.object(Path, "home", staticmethod(lambda: home)):
            roots = scaffold.known_project_roots()
        check("a missing project root is dropped", gone.resolve() not in roots)
        check("a relative key is dropped",
              all(r.is_absolute() for r in roots))
        check("the same tree under both separators counts once",
              len(roots) == 1, f"got {[str(r) for r in roots]}")

        # An unreadable config is a machine with nothing to report, not a
        # crash: this backs a diagnostic that must survive a broken file.
        (home / ".claude.json").write_text("{ not json", encoding="utf-8")
        with patch.object(Path, "home", staticmethod(lambda: home)):
            check("unreadable config yields nothing, not an error",
                  scaffold.known_project_roots() == [])

    def project(name: str, *, mcp: dict | None = None,
                template: dict | None = None,
                hooks: dict | None = None) -> Path:
        root = Path(tmp_root) / name
        (root / ".claude").mkdir(parents=True)
        if mcp is not None:
            (root / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
        if template is not None:
            (root / ".mcp.json.template").write_text(json.dumps(template),
                                                     encoding="utf-8")
        if hooks is not None:
            (root / ".claude" / "settings.json").write_text(json.dumps(hooks),
                                                            encoding="utf-8")
        return root

    with tempfile.TemporaryDirectory() as tmp_root:
        current = project("current", mcp={"mcpServers": {_INSTANCE: {
            "type": "http",
            "url": f"http://127.0.0.1:8918/mcp?token={_FAKE_TOKEN}",
        }}})
        legacy_stdio = project("legacy", template={"mcpServers": {
            _LEGACY_INSTANCE: {"type": "stdio",
                               "command": "${HOME}/.claude/mnemo/bin/mnemo",
                               "args": ["mcp"]},
        }})
        hooked = project("hooked", mcp={"mcpServers": {_INSTANCE: {
            "type": "http", "url": "http://127.0.0.1:8918/mcp?token=" + _FAKE_TOKEN,
        }}}, hooks={"hooks": {"PostToolUse": [
            {"hooks": [{"type": "command",
                        "command": "~/.claude/mnemo/bin/mnemo hook-postedit"}]}
        ]}})
        placeholder = project("placeholder", template={"mcpServers": {
            _INSTANCE: {"type": "http",
                        "url": "http://127.0.0.1:{{MNEMO_PORT}}"
                               "/mcp?token={{MNEMO_TOKEN}}"},
        }})
        foreign = project("foreign", mcp={"mcpServers": {
            "some-other-server": {"type": "stdio", "command": "node",
                                  "args": ["server.js"]},
        }})
        bare = project("bare")

        roots = [current, legacy_stdio, hooked, placeholder, foreign, bare]
        found = {p.root.name: p for p in scaffold.adopted_projects(roots)}

        check("a project with no wiring is not reported", "bare" not in found)
        check("somebody else's MCP server is not mnemo's",
              "foreign" not in found)
        check("a current HTTP entry is found", "current" in found)
        check("a current HTTP entry needs no --migrate",
              "current" in found and not found["current"].migrate)
        check("an L2 stdio entry demands --migrate",
              "legacy" in found and found["legacy"].migrate)
        check("a retired hook demands --migrate",
              "hooked" in found and found["hooked"].migrate)
        check("--migrate shows up in the printed command",
              "legacy" in found and "--migrate" in found["legacy"].command())
        check("a current project's command has no --migrate",
              "current" in found and "--migrate" not in found["current"].command())

        check("the literal token is read back",
              "current" in found and found["current"].token == _FAKE_TOKEN)
        check("a placeholder is never mistaken for a token",
              "placeholder" in found and found["placeholder"].token is None)
        for name, proj in found.items():
            check(f"{name}: no token in the printed command",
                  _FAKE_TOKEN not in proj.command())


def test_removal_lifts_the_queue_cancellation() -> None:
    """Removing a bank must not poison the next bank at the same root.

    Found the first time the cabinet could remove a bank at all: remove one,
    re-register the same folder, and it indexed nothing. Status `empty`,
    queue depth 0, an empty index log, and `reindex` cheerfully answering
    "queued 1 task(s)".

    The mechanism is that a bank id is DERIVED — sha1 of the canonical root
    — so the same folder always comes back with the same id. `drop_bank`
    puts that id in the queue's `_cancelled` set to quiet the worker before
    the index file is unlinked, and `enqueue` answers a cancelled bank by
    returning a task id and dropping the task on the floor. Every failure
    path lifted the cancellation; the success path did not, so the id stayed
    cancelled for the life of the process and the next bank inherited it.
    """
    print("\n=== removal lifts the queue cancellation ===")

    from src import api, workqueue

    bank_id = "0123456789abcdef"
    made = [0]

    def bulk() -> object:
        made[0] += 1
        return workqueue.Task(id=f"t{made[0]}", bank_id=bank_id, kind="bulk",
                              priority=workqueue.Priority.NORMAL,
                              trigger="api")

    workqueue.resume_bank(bank_id)          # start from a known state
    workqueue.enqueue(bulk())
    check("a live bank accepts work", workqueue.depth(bank_id) > 0)
    workqueue.clear()

    workqueue.drop_bank(bank_id)
    workqueue.enqueue(bulk())
    check("a cancelled bank silently drops work",
          workqueue.depth(bank_id) == 0)
    check("...which is exactly why it must be lifted",
          workqueue.is_cancelled(bank_id))

    # What `api_remove_bank` does on its way out, and the assertion is that
    # it does it at all: before the fix, only the two failure paths did.
    workqueue.resume_bank(bank_id)
    check("resume_bank lifts it", not workqueue.is_cancelled(bank_id))
    workqueue.enqueue(bulk())
    check("a re-registered bank at the same root indexes again",
          workqueue.depth(bank_id) > 0)
    workqueue.clear()

    # The guard that makes this a regression test rather than a description
    # of the queue: the removal endpoint must reach `resume_bank` on the
    # success path, not only when it refuses.
    import inspect

    source = inspect.getsource(api.api_remove_bank)
    tail = source.split("registry.remove(")[-1]
    check("api_remove_bank lifts the cancellation after removing the bank",
          "resume_bank" in tail,
          "resume_bank is only reached on the failure paths")
    check("`q` is resolved outside the drop_index branch",
          source.index("q = _queue()") < source.index("if drop_index:"),
          "q would be undefined when drop_index=False")


def test_setup_scripts_agree() -> None:
    """The two regeneration scripts must produce identical bytes.

    They exist as a pair because the shell half needs bash, which a native
    Windows machine has no reason to have. A pair is also how they drift: two
    implementations of one substitution, edited at different times. This is
    the check that notices.

    The values are chosen to break naive implementations — a `|` would end a
    `sed` expression, a `$1` would be read as a capture reference by
    PowerShell's `-replace`, and both halves deliberately use neither.
    """
    print("\n=== setup scripts agree ===")
    import subprocess

    from src.scaffold import _SETUP_MARKER, _SETUP_PS1, _SETUP_SH

    def usable_bash() -> list[str] | None:
        """A bash that runs a script — not merely the name `bash` on PATH.

        On a GitHub Windows runner `bash` resolves to the WSL launcher in
        System32, ahead of Git's own. With no distribution installed it exits
        1 and writes nothing to stderr, which is indistinguishable from the
        script failing: two checks below then passed vacuously and a third
        failed for a reason that was never about the script. So probe it —
        a bash that cannot echo cannot test anything.
        """
        candidates = [["bash"]]
        if os.name == "nt":
            candidates.append([r"C:\Program Files\Git\bin\bash.exe"])
        for cand in candidates:
            try:
                probe = subprocess.run(cand + ["-c", "printf ok"],
                                       capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                continue
            if probe.returncode == 0 and probe.stdout.strip() == b"ok":
                try:
                    ver = subprocess.run(cand + ["--version"],
                                         capture_output=True, timeout=60)
                    first = ver.stdout.decode("utf-8", "replace").splitlines()
                except (OSError, subprocess.SubprocessError):
                    first = []
                print(f"note  shell half via {cand[0]}"
                      f"{' — ' + first[0] if first else ''}")
                return cand
        return None

    bash = usable_bash()
    if bash is None:
        print("note  no bash on this machine runs a script — sh half skipped")

    check("both scripts carry the marker",
          _SETUP_MARKER in _SETUP_SH and _SETUP_MARKER in _SETUP_PS1)

    template = json.dumps({"mcpServers": {
        "mnemo-memory": {"type": "http",
                         "url": "http://{{MNEMO_HOST}}:{{MNEMO_PORT}}"
                                "/mcp?token={{MNEMO_TOKEN}}"},
        "mnemo-notes": {"type": "http",
                        "url": "http://{{MNEMO_HOST}}:{{MNEMO_PORT}}"
                               "/mcp?token={{MNEMO_NOTES_TOKEN}}"},
        "foreign": {"type": "stdio", "command": "{{ODD_VALUE}}"},
    }}, indent=2) + "\n"
    env = (
        "# a comment\n"
        "\n"
        "MNEMO_HOST=127.0.0.1\n"
        "  MNEMO_PORT = 8918  \n"                       # spaces both sides
        f'MNEMO_TOKEN="{_FAKE_TOKEN}"\n'                # double quoted
        f"MNEMO_NOTES_TOKEN='{_FAKE_TOKEN}'\n"          # single quoted
        "ODD_VALUE=a|b&c$1d/e\n"                        # hostile to sed/regex
        "MNEMO_TOKEN=later-definition-must-lose\n"
    )

    def render(script: str, name: str, runner: list[str]) -> bytes | None:
        with tempfile.TemporaryDirectory(prefix="mnemo setup ") as raw:
            proj = Path(raw)
            _write(proj / ".mcp.json.template", template)
            _write(proj / ".mcp.env", env)
            _write(proj / name, script)
            try:
                done = subprocess.run(runner + [str(proj / name)], cwd=str(proj),
                                      capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                return None
            if done.returncode != 0:
                print(f"note  {name} exited {done.returncode}: "
                      f"{done.stderr.decode('utf-8', 'replace').strip()[:120]}")
                return None
            out = proj / ".mcp.json"
            return out.read_bytes() if out.exists() else b""

    from_sh = render(_SETUP_SH, "mcp-setup.sh", bash) if bash else None
    from_ps = render(_SETUP_PS1, "mcp-setup.ps1",
                     ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                      "-File"])

    if from_sh is None or from_ps is None:
        missing = "bash" if from_sh is None else "powershell"
        print(f"note  parity check skipped: no usable {missing}")
    else:
        check("both halves write byte-identical output", from_sh == from_ps,
              detail=f"{len(from_sh)} vs {len(from_ps)} bytes")

    survivor = from_sh if from_sh is not None else from_ps
    if survivor is not None:
        doc = json.loads(survivor.decode("utf-8"))
        url = doc["mcpServers"]["mnemo-memory"]["url"]
        check("a value padded around `=` is trimmed", ":8918/" in url,
              detail=url)
        check("a quoted value loses exactly its quotes",
              url.endswith(_FAKE_TOKEN), detail=url)
        check("a single-quoted value too",
              doc["mcpServers"]["mnemo-notes"]["url"].endswith(_FAKE_TOKEN))
        check("the first definition wins", "later-definition" not in
              survivor.decode("utf-8"))
        check("a value full of metacharacters survives intact",
              doc["mcpServers"]["foreign"]["command"] == "a|b&c$1d/e",
              detail=doc["mcpServers"]["foreign"]["command"])
        check("no placeholder is left behind",
              not re.search(r"\{\{[A-Z0-9_]+\}\}", survivor.decode("utf-8")))

    # The failure that matters: a placeholder with no value must be loud. The
    # generation before this listed substitutions by hand, so a forgotten one
    # was copied through verbatim while the script still exited 0.
    def render_missing(script: str, name: str, runner: list[str]):
        with tempfile.TemporaryDirectory(prefix="mnemo setup ") as raw:
            proj = Path(raw)
            _write(proj / ".mcp.json.template", template)
            _write(proj / ".mcp.env", "MNEMO_HOST=127.0.0.1\n")
            _write(proj / name, script)
            try:
                done = subprocess.run(runner + [str(proj / name)], cwd=str(proj),
                                      capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                return None
            return done.returncode, (proj / ".mcp.json").exists(), \
                done.stderr.decode("utf-8", "replace")

    halves = [(_SETUP_PS1, "mcp-setup.ps1",
               ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File"])]
    if bash:
        halves.insert(0, (_SETUP_SH, "mcp-setup.sh", bash))
    for script, name, runner in halves:
        result = render_missing(script, name, runner)
        if result is None:
            continue
        code, wrote, stderr = result
        check(f"{name} fails on a missing variable", code != 0, detail=str(code))
        check(f"{name} writes nothing when it fails", not wrote)
        check(f"{name} names every missing variable",
              "MNEMO_PORT" in stderr and "MNEMO_TOKEN" in stderr
              and "ODD_VALUE" in stderr, detail=stderr.strip()[:160])


def test_scaffold_gitignore() -> None:
    """The plain branch: a literal token means git must not carry the file."""
    with tempfile.TemporaryDirectory(prefix="mnemo ignore ") as raw:
        proj = Path(raw) / "plain"
        (proj / ".claude").mkdir(parents=True)
        gitignore = proj / ".gitignore"
        _write(gitignore, "venv/\n*.pyc\n")

        wiring = _plan_wiring(proj, token=_FAKE_TOKEN,
                              migrate=False)
        written = {path.name: text for path, text in wiring.writes}
        # `init` builds the template layer now, in every project. `.mcp.json`
        # is a build product from here on and is never written directly.
        check("the template layer is seeded",
              {".mcp.json.template", "mcp-setup.sh", "mcp-setup.ps1",
               ".mcp.env", ".mcp.env.example"} <= set(written),
              detail=str(sorted(written)))
        check(".mcp.json itself is never written",
              ".mcp.json" not in written, detail=str(sorted(written)))

        text = written.get(".gitignore", "")
        ignored = {line.strip() for line in text.splitlines()}
        # BOTH files can end up holding a literal token: `.mcp.env` because
        # that is where it lives, and `.mcp.json` because the setup scripts
        # substitute it in.
        check(".gitignore gains .mcp.json", ".mcp.json" in ignored, detail=text)
        check(".gitignore gains .mcp.env", ".mcp.env" in ignored, detail=text)
        # Only those, and the existing lines untouched in place: a .gitignore
        # is human-curated and git-tracked, so the smallest possible edit is
        # the only acceptable one.
        added = [l for l in text.splitlines()
                 if l.strip() and not l.startswith("#")
                 and l not in ("venv/", "*.pyc")]
        check("no other .gitignore line is added or reordered",
              added == [".mcp.json", ".mcp.env"]
              and text.startswith("venv/\n*.pyc\n"),
              detail=text)

        _write(gitignore, text)
        again = _plan_wiring(proj, token=_FAKE_TOKEN,
                             migrate=False)
        check(".gitignore edit is idempotent",
              all(p.name != ".gitignore" for p, _ in again.writes),
              detail=str([p.name for p, _ in again.writes]))


def test_scaffold_refuses_a_tracked_file() -> None:
    """A literal bank token is never written into a git-tracked file.

    A REFUSAL, not a warning, and the asymmetry is the reason: a refusal costs
    one command to undo, while a token committed into a tracked file cannot be
    undone in any useful sense — by the time anyone notices, it is in somebody
    else's clone.

    Uses a real repository, because the thing under test is a real git index.
    """
    import subprocess

    def git(cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    with tempfile.TemporaryDirectory(prefix="mnemo tracked ") as raw:
        proj = Path(raw) / "repo"
        proj.mkdir()
        try:
            git(proj, "init", "-q")
        except (OSError, subprocess.CalledProcessError):
            print("SKIP  tracked-file refusal: no usable git binary")
            return

        # A repository with NOTHING staged has no .git/index at all. That must
        # read as "nothing is tracked", not as "unknown" — otherwise `init`
        # refuses in a brand-new repo, which is exactly where it gets run.
        check("a repo with an empty index is not 'unknown'",
              _git_tracked(proj, ".mcp.json") is False,
              detail=str(_git_tracked(proj, ".mcp.json")))
        wiring = _plan_wiring(proj, token=_FAKE_TOKEN, migrate=False)
        check("init plans the template layer in an untracked repo",
              any(p.name == ".mcp.json.template" for p, _ in wiring.writes),
              detail=str([p.name for p, _ in wiring.writes]))

        # Now track it. `.mcp.json` is a build product — the setup scripts
        # substitute the literal token into it — so a tracked one is a token
        # about to be committed by the next regeneration.
        _write(proj / ".mcp.json", '{"mcpServers": {}}\n')
        git(proj, "add", "-f", ".mcp.json")
        check("a tracked file reads as tracked",
              _git_tracked(proj, ".mcp.json") is True)

        # It raises `_NeedsUntrack`, not `_Refuse`: the outcomes differ. A
        # refusal ends the run; this is a question `init` can answer itself
        # with one reversible git command, once someone has said yes. What has
        # NOT changed is that planning writes nothing until it is settled.
        for label, migrate in (("init", False), ("--migrate", True)):
            raised, name, wrong = None, "", ""
            try:
                _plan_wiring(proj, token=_FAKE_TOKEN, migrate=migrate)
            except _NeedsUntrack as exc:
                raised, name = "untrack", exc.name
            except _Refuse as exc:
                raised, wrong = "refuse", str(exc)
            check(f"{label} stops on a tracked .mcp.json",
                  raised == "untrack", detail=f"{raised} {wrong[:120]}")
            check(f"{label} names the file it must untrack",
                  name == ".mcp.json", detail=name)

        # The other file that gets a literal token, and it is the file the
        # token actually lives in.
        _write(proj / ".mcp.json.template", '{"mcpServers": {}}\n')
        git(proj, "rm", "--cached", "-q", ".mcp.json")
        _write(proj / ".mcp.env", "MNEMO_PORT=8918\n")
        git(proj, "add", "-f", ".mcp.env")
        raised, name = None, ""
        try:
            _plan_wiring(proj, token=_FAKE_TOKEN, migrate=False)
        except _NeedsUntrack as exc:
            raised, name = "untrack", exc.name
        except _Refuse:
            raised = "refuse"
        check("a tracked .mcp.env stops it too", raised == "untrack",
              detail=str(raised))
        check("and names .mcp.env", name == ".mcp.env", detail=name)


def test_git_index_probe() -> None:
    """`init` answers "is this tracked?" by reading .git/index, never by git.

    Exercised against **this repository**, which is the only git index on hand
    that is guaranteed to exist and to have known contents.
    """
    repo = Path(__file__).resolve().parent.parent
    check("a tracked file reads as tracked",
          _git_tracked(repo, "CLAUDE.md") is True)
    check("a nested tracked file reads as tracked",
          _git_tracked(repo, "src/api.py") is True)
    check("an absent file reads as untracked",
          _git_tracked(repo, "no-such-file.txt") is False)
    with tempfile.TemporaryDirectory(prefix="mnemo nogit ") as raw:
        check("a directory outside any repository reads as untracked",
              _git_tracked(Path(raw), ".mcp.json") is False)


def test_project_resolution() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo roots ") as raw:
        base = Path(raw)
        explicit = base / "explicit"
        mnemo_root = base / "env-root"
        claude_root = base / "claude-root"
        for path in (explicit, mnemo_root, claude_root):
            path.mkdir()

        with patch.dict(
            os.environ,
            {
                "MNEMO_ROOT": str(mnemo_root),
                "CLAUDE_PROJECT_DIR": str(claude_root),
            },
            clear=False,
        ):
            check(
                "explicit root wins",
                config.resolve(explicit).root == explicit.resolve(),
            )
            check(
                "MNEMO_ROOT beats CLAUDE_PROJECT_DIR",
                config.resolve(None).root == mnemo_root.resolve(),
            )

        with patch.dict(
            os.environ,
            {"CLAUDE_PROJECT_DIR": str(claude_root)},
            clear=False,
        ):
            os.environ.pop("MNEMO_ROOT", None)
            check(
                "CLAUDE_PROJECT_DIR fallback",
                config.resolve(None).root == claude_root.resolve(),
            )


def test_index_paths() -> None:
    """A bank is FLAT: every .md under the root, wherever it sits.

    The fixture deliberately reaches outside `.claude/` — under the old
    scope-based walk those files were invisible, so a walk that still
    honoured scopes passes the identifier checks while indexing nothing.
    """
    with tempfile.TemporaryDirectory(prefix="mnemo paths ") as raw:
        root = Path(raw) / "проєкт"
        memory = root / ".claude" / "memory" / "nested"
        agent = root / ".claude" / "memory" / "agents" / "reviewer"
        docs = root / "docs" / "deep dive"
        for d in (memory, agent, docs):
            d.mkdir(parents=True)
        (memory / "topic.md").write_text("# Topic\n", encoding="utf-8")
        (agent / "MEMORY.md").write_text("# Reviewer\n", encoding="utf-8")
        (root / "README.md").write_text("# Readme\n", encoding="utf-8")       # bank root
        (docs / "нотатка.md").write_text("# Нотатка\n", encoding="utf-8")     # outside .claude/
        (root / "notes.txt").write_text("not markdown\n", encoding="utf-8")
        (docs / "diagram.markdown").write_text("not .md\n", encoding="utf-8")

        # A bank pointed at a project root must not drown in vendor docs
        # (config.DEFAULT_EXCLUDE), at the root or nested — but the patterns
        # name directories, so a file merely *starting* with "venv" stays.
        vendored = [
            ".venv/Lib/site-packages/pkg/README.md",
            "venv/x.md",
            "node_modules/a/readme.md",
            "sub/node_modules/b/readme.md",
            "__pycache__/c.md",
            "sub/__pycache__/d.md",
            ".git/e.md",
        ]
        kept = ["venv-notes.md", "docs/venv.md"]
        for rel in vendored + kept:
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# vendor\n", encoding="utf-8")

        # `config.resolve` calls `Path.resolve()`, so the bank root the walk
        # actually sees is the real path — which is NOT `root` wherever the
        # temp directory is reached through a link or a short name (macOS
        # `/var` → `/private/var`, a CI runner's `RUNNER~1`). Compare against
        # the resolved root, or the identifier check fails on those machines
        # and passes on the developer's.
        bank_root = config.resolve(root).root

        walk = scan_bank(bank_root)
        check(
            "vendor directories are excluded at any depth",
            not [rel for rel in vendored if rel in walk],
            detail=str([rel for rel in vendored if rel in walk]),
        )
        check(
            "exclusion matches directory names, not name prefixes",
            all(rel in walk for rel in kept),
            detail=str([rel for rel in kept if rel not in walk]),
        )
        for rel in vendored + kept:
            (root / rel).unlink()

        # Back to just the curated fixture for the strict identifier list.
        walk = scan_bank(bank_root)
        identifiers = sorted(walk)
        check(
            "flat walk takes every .md under the bank root",
            identifiers == [
                ".claude/memory/agents/reviewer/MEMORY.md",
                ".claude/memory/nested/topic.md",
                "README.md",
                "docs/deep dive/нотатка.md",
            ],
            detail=str(identifiers),
        )
        check(
            "flat walk takes only .md (not .txt, not .markdown)",
            not any(not v.endswith(".md") for v in identifiers),
            detail=str(identifiers),
        )
        check(
            "stored identifiers contain no backslashes",
            all("\\" not in value for value in identifiers),
            detail=str(identifiers),
        )
        check(
            "stored identifiers are relative to the bank root",
            all(not Path(value).is_absolute() for value in identifiers)
            and all(walk[v].abs_path == bank_root / v for v in identifiers),
            detail=str(identifiers),
        )
        # `scan_bank` sorts Path objects, so key order is normcase-folded on
        # Windows and byte-ordered on POSIX — the two platforms disagree.
        # What the indexer actually relies on is that ONE machine repeats
        # itself, so assert that, not a cross-platform order.
        check(
            "walk order is stable across calls",
            list(scan_bank(bank_root)) == list(walk),
            detail=str(list(walk)),
        )


def test_model_cache_validation() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo cache ") as raw:
        cache = Path(raw)
        required = {
            "config.json",
            "model.onnx",
            "model.onnx_data",
            "tokenizer.json",
            "tokenizer_config.json",
        }
        wrong_snapshot = (
            cache / "models--vendor--different-model" / "snapshots" / "revision"
        )
        near_snapshot = (
            cache
            / "models--user--qdrant--multilingual-e5-large-onnx-copy"
            / "snapshots"
            / "revision"
        )
        right_snapshot = (
            cache
            / "models--qdrant--multilingual-e5-large-onnx"
            / "snapshots"
            / "revision"
        )

        with patch.object(embedder, "MODEL_CACHE", cache):
            check("empty model cache is not warmed", not embedder.is_model_cached())

            wrong_snapshot.mkdir(parents=True)
            for name in required:
                (wrong_snapshot / name).write_bytes(b"complete")
            check(
                "complete different model is not accepted",
                not embedder.is_model_cached(),
            )

            near_snapshot.mkdir(parents=True)
            for name in required:
                (near_snapshot / name).write_bytes(b"complete")
            check(
                "similar repository name is not accepted",
                not embedder.is_model_cached(),
            )

            right_snapshot.mkdir(parents=True)
            (right_snapshot / "model.onnx").write_bytes(b"partial")
            check("partial model cache is not warmed", not embedder.is_model_cached())
            for name in required:
                (right_snapshot / name).write_bytes(b"complete")
            check("complete model cache is warmed", embedder.is_model_cached())


def test_orphan_indexes() -> None:
    """`state/` holds index files keyed by a hash of a root; nothing links one
    back to a bank. These checks pin the two properties that make deleting
    them safe: only unclaimed files are listed, and an unreadable registry
    refuses rather than declaring everything unclaimed."""
    from src import registry

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        state.mkdir()
        with patch.object(config, "STATE_DIR", state):
            bank_root = Path(tmp) / "bank"
            bank_root.mkdir()
            bank = registry.add(bank_root, name="live")

            bank.db_path.write_bytes(b"")
            (state / "service.db").write_bytes(b"")
            orphan = state / "deadbeefdeadbeef.db"
            orphan.write_bytes(b"y" * 64)

            found = registry.orphan_indexes()
            ids = {o.id for o in found}
            check("only the unclaimed index is an orphan",
                  ids == {"deadbeefdeadbeef"}, f"got {sorted(ids)}")
            check("an orphan's reported size is its on-disk size",
                  next(o.size for o in found) == 64)
            check("listing deletes nothing", orphan.exists())

            # Siblings are created after the listing on purpose: opening a
            # database makes SQLite clear a stale -wal (see `store.probe`), so
            # a junk sibling would not survive the probe to be counted here.
            orphan.with_name(orphan.name + "-wal").write_bytes(b"x" * 10)
            orphan.with_name(orphan.name + "-shm").write_bytes(b"x" * 10)

            # The journal is excluded by name. Deriving "not a bank" from its
            # contents would be one inference away from deleting the log.
            failed_service = False
            try:
                registry.delete_index("service")
            except ValueError:
                failed_service = True
            check("delete_index refuses service.db", failed_service)

            refused_live = False
            try:
                registry.delete_index(bank.id)
            except ValueError:
                refused_live = True
            check("delete_index refuses a registered bank", refused_live)
            check("the registered bank's index survives", bank.db_path.exists())

            removed, locked = registry.delete_index("deadbeefdeadbeef")
            check("deleting an orphan takes its siblings too",
                  removed == 3 and not locked
                  and not orphan.exists()
                  and not orphan.with_name(orphan.name + "-wal").exists()
                  and not orphan.with_name(orphan.name + "-shm").exists(),
                  f"removed={removed} locked={locked}")
            check("service.db is still there", (state / "service.db").exists())
            check("nothing is orphaned once it is gone",
                  registry.orphan_indexes() == [])

            # A bank registered between listing and deleting must survive a
            # stale list — delete_index re-reads the registry for this.
            second_root = Path(tmp) / "second"
            second_root.mkdir()
            later = registry.add(second_root, name="later")
            later.db_path.write_bytes(b"")
            stale = False
            try:
                registry.delete_index(later.id)
            except ValueError:
                stale = True
            check("a stale list cannot delete a newly registered bank",
                  stale and later.db_path.exists())

            # Fail-safe: unparseable registry -> raise, never "no banks".
            registry.banks_file().write_text("{ not json", encoding="utf-8")
            refused = False
            try:
                registry.orphan_indexes()
            except Exception:  # noqa: BLE001 - any refusal will do
                refused = True
            check("an unreadable registry refuses instead of listing every "
                  "index as an orphan", refused)


def test_bank_resolution() -> None:
    """`resolve` looks both ways: up for the bank containing a path, down for
    the bank a project root contains. The canonical layout puts memory *below*
    the project, so without the second direction the obvious invocation —
    `mnemo search` from the project root — matched nothing."""
    from src import registry

    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        state.mkdir()
        with patch.object(config, "STATE_DIR", state):
            proj = Path(tmp) / "proj"
            bank_root = proj / ".claude" / "memory"
            bank_root.mkdir(parents=True)
            (bank_root / "logs").mkdir()
            bank = registry.add(bank_root, name="proj-bank")

            check("a path inside the bank resolves upwards",
                  registry.resolve(str(bank_root / "logs")).id == bank.id)
            check("the bank root itself resolves",
                  registry.resolve(str(bank_root)).id == bank.id)
            check("the PROJECT root resolves downwards to its one bank",
                  registry.resolve(str(proj)).id == bank.id)
            check("an intermediate folder resolves downwards too",
                  registry.resolve(str(proj / ".claude")).id == bank.id)

            # Two banks under one folder: refuse, never guess.
            other_root = Path(tmp) / "proj" / "second" / "mem"
            other_root.mkdir(parents=True)
            registry.add(other_root, name="second")
            ambiguous = False
            try:
                registry.resolve(str(proj))
            except registry.AmbiguousBankRef:
                ambiguous = True
            check("a folder holding several banks refuses instead of guessing",
                  ambiguous)
            check("naming one of them still works",
                  registry.resolve("second").name == "second")

            # The service's cwd is not the caller's, so `resolve` treats only
            # an absolute path as a path. Making a relative ref absolute is
            # `cli._bank_ref`'s job, and a bare word must survive it as a name
            # — otherwise a mistyped name becomes `<cwd>/typo` and, from inside
            # some bank's tree, resolves to that bank: the wrong memory,
            # silently.
            from src.cli import _bank_ref

            relative = False
            typo_stays_a_name = False
            cwd = os.getcwd()
            try:
                os.chdir(bank_root / "logs")
                relative = Path(_bank_ref("..")).is_absolute()
                typo_stays_a_name = _bank_ref("proj-bnak") == "proj-bnak"
                check("a bare cwd default is sent absolute",
                      Path(_bank_ref(None)).is_absolute())
            finally:
                os.chdir(cwd)
            check("the client makes a relative path absolute", relative)
            check("a bare word is left as a name, not joined to the cwd",
                  typo_stays_a_name)

            unresolved = False
            try:
                registry.resolve("proj-bnak")
            except registry.BankNotFound:
                unresolved = True
            check("a mistyped bank name resolves to nothing", unresolved)

            relative_refused = False
            try:
                registry.resolve(".claude/memory")
            except registry.BankNotFound:
                relative_refused = True
            check("the service never interprets a relative ref as a path",
                  relative_refused)

            # A disabled bank stays addressable by name, invisible to paths.
            registry.update(bank.id, enabled=False)
            check("a disabled bank still resolves by name",
                  registry.resolve("proj-bank").id == bank.id)
            gone = False
            try:
                registry.resolve(str(bank_root / "logs"))
            except registry.BankNotFound:
                gone = True
            check("a disabled bank is invisible to the path form", gone)


def test_env_file() -> None:
    """``<state>/mnemo.env`` reaches a process that inherited no environment.

    The bug it fixes is invisible from a shell: ``systemd --user`` and a
    launchd LaunchAgent start the service with a bare environment and never
    read ``~/.profile``, so every ``MNEMO_*`` override was silently ignored
    by the one process that reads most of them. Asserted in a subprocess
    because config evaluates its knobs once, at import.
    """
    import subprocess

    def knobs(state: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("MNEMO_BATCH_SIZE", None)
        env["MNEMO_STATE_DIR"] = str(state)
        env.update(extra or {})
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys, json; sys.path.insert(0, sys.argv[1]);"
             "from src import config;"
             "print(json.dumps({'batch': config.BATCH_SIZE,"
             " 'file': str(config.ENV_FILE) if config.ENV_FILE else ''}))",
             str(Path(__file__).resolve().parent.parent)],
            capture_output=True, text=True, env=env,
        )
        return json.loads(result.stdout.strip().splitlines()[-1])

    with tempfile.TemporaryDirectory(prefix="mnemo env ") as raw:
        state = Path(raw)
        (state / "mnemo.env").write_text(
            '# a comment, and a blank line follow\n\n'
            'MNEMO_BATCH_SIZE="64"\n',
            encoding="utf-8",
        )

        seen = knobs(state)
        check("mnemo.env is read when nothing set the variable",
              seen["batch"] == 64, f"batch={seen['batch']}")
        check("the file that was read is reported",
              seen["file"].endswith("mnemo.env"), seen["file"])

        # Precedence: exporting a variable for one run must still win, or a
        # stored default would quietly override a deliberate override.
        seen = knobs(state, {"MNEMO_BATCH_SIZE": "7"})
        check("a real environment variable beats the file",
              seen["batch"] == 7, f"batch={seen['batch']}")

        seen = knobs(state / "absent")
        check("an absent mnemo.env is not an error",
              seen["batch"] == 16 and seen["file"] == "",
              f"batch={seen['batch']} file={seen['file']!r}")


def test_machine_settings() -> None:
    """env > file > default, and nothing freezes a value at import time."""
    print("\n=== machine settings ===")
    import subprocess

    from src import settings as settings_mod

    def resolved(state: Path, extra: dict[str, str] | None = None) -> dict:
        """Resolve settings in a FRESH interpreter.

        A subprocess, not a monkeypatch, because the bug this guards against
        is exactly an import-time binding: inside this process `config` is
        already imported and would hide it.
        """
        env = dict(os.environ)
        for key in list(env):
            if key.startswith("MNEMO_"):
                env.pop(key)
        env["MNEMO_STATE_DIR"] = str(state)
        env.update(extra or {})
        result = subprocess.run(
            [sys.executable, "-c",
             "import sys, json; sys.path.insert(0, sys.argv[1]);"
             "from src import settings;"
             "from src.providers import get_provider;"
             "print(json.dumps({"
             " 'provider': settings.provider(),"
             " 'url': settings.api_url(),"
             " 'dim': settings.api_dim(),"
             " 'source': settings.effective()['provider'].source,"
             " 'instance': get_provider().name,"
             "}))",
             str(Path(__file__).resolve().parent.parent)],
            capture_output=True, text=True, env=env,
        )
        if not result.stdout.strip():
            check("the settings probe ran at all", False, detail=result.stderr[-400:])
            return {}
        return json.loads(result.stdout.strip().splitlines()[-1])

    with tempfile.TemporaryDirectory(prefix="mnemo settings ") as raw:
        state = Path(raw)

        seen = resolved(state)
        check("an absent settings.json is not an error",
              seen.get("provider") == "local" and seen.get("source") == "default",
              detail=str(seen))

        (state / "settings.json").write_text(
            json.dumps({"version": 1, "provider": "api",
                        "api": {"url": "http://127.0.0.1:11434/v1/embeddings",
                                "model": "bge-m3", "dim": 1024},
                        "note": "a human left this here"}),
            encoding="utf-8",
        )
        seen = resolved(state)
        check("a stored provider is read from the file",
              seen.get("provider") == "api" and seen.get("source") == "file",
              detail=str(seen))
        check("and the endpoint comes with it",
              seen.get("dim") == 1024 and "11434" in str(seen.get("url")),
              detail=str(seen))
        # The whole point of resolving in a subprocess: `get_provider()` must
        # honour the stored value, not the one bound when the module loaded.
        check("get_provider follows the file, not an import-time constant",
              seen.get("instance") == "api", detail=str(seen))

        seen = resolved(state, {"MNEMO_PROVIDER": "local"})
        check("a real environment variable beats the file",
              seen.get("provider") == "local" and seen.get("source") == "env",
              detail=str(seen))

        # Saving must not eat a key we do not own — same rule as banks.json.
        os.environ["MNEMO_SETTINGS_FILE"] = str(state / "settings.json")
        try:
            settings_mod.load(force=True)
            settings_mod.save({"provider": "local"})
            doc = json.loads((state / "settings.json").read_text(encoding="utf-8"))
        finally:
            os.environ.pop("MNEMO_SETTINGS_FILE", None)
            settings_mod.load(force=True)
        check("saving preserves a hand-written key",
              doc.get("note") == "a human left this here", detail=str(doc))
        check("and keeps the rest of the endpoint",
              (doc.get("api") or {}).get("dim") == 1024, detail=str(doc))
        check("while writing the new value",
              doc.get("provider") == "local", detail=str(doc))

        # A secret must never come back out of the settings endpoint.
        os.environ["MNEMO_SETTINGS_FILE"] = str(state / "settings.json")
        os.environ["MNEMO_API_EMBED_KEY"] = "sk-secret-value"
        try:
            settings_mod.load(force=True)
            report = settings_mod.effective()
        finally:
            os.environ.pop("MNEMO_API_EMBED_KEY", None)
            os.environ.pop("MNEMO_SETTINGS_FILE", None)
            settings_mod.load(force=True)
        check("the key is reported as set, never echoed",
              report["api.key_set"].value is True
              and "sk-secret-value" not in json.dumps(
                  {k: v.value for k, v in report.items()}),
              detail=str({k: v.value for k, v in report.items()}))


def main() -> int:
    test_scaffold()
    test_scaffold_template_project()
    test_scaffold_drops_the_bank_segment()
    test_setup_script_refresh()
    test_scaffold_hand_edited_sed_line()
    test_scaffold_renames_the_legacy_key()
    test_memory_rule_refresh()
    test_adopted_project_discovery()
    test_removal_lifts_the_queue_cancellation()
    test_setup_scripts_agree()
    test_scaffold_gitignore()
    test_scaffold_refuses_a_tracked_file()
    test_git_index_probe()
    test_project_resolution()
    test_index_paths()
    test_orphan_indexes()
    test_bank_resolution()
    test_model_cache_validation()
    test_env_file()
    test_machine_settings()
    print(
        f"\n{_passed} passed, {_failed} failed, "
        f"{_xfailed} xfailed (awaiting a later phase), {_xpassed} xpassed"
    )
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
