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
from src.scaffold import (  # noqa: E402
    _Refuse,
    _git_tracked,
    _plan_mcp,
    _plan_settings,
    _plan_wiring,
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
    check(
        "MCP url is loopback",
        isinstance(url, str)
        and re.fullmatch(r"http://(127\.0\.0\.1|localhost|\[::1\]):\S+", url)
        is not None,
        detail=str(url),
    )
    url = url if isinstance(url, str) else ""

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
        for var in ("{{MNEMO_PORT}}", "{{MNEMO_TOKEN}}"):
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


def _check_hook_wiring(settings: dict) -> None:
    """Every event named explicitly — a hook group dropped from the source
    must fail here, not vanish along with the loop that iterated it.

    Called with the plan for a run that **asked for both seeds**: `init`
    itself writes no hook (asserted separately), so this checks the shape a
    seed takes once requested.

    `ingest` and `hook-postedit` were the v2 wiring and indexed inline, inside
    the user's session; phase 4 retired them in favour of the watcher. Their
    absence is asserted below and is the more valuable half of this function:
    it is what catches a regression that quietly reintroduces synchronous
    indexing.
    """
    expected = {
        "SessionStart": ("memory-hook", None),
        "UserPromptSubmit": ("hook-inject", None),
    }
    retired = {
        "SessionStart": "ingest",
        "PostToolUse": "hook-postedit",
    }
    hooks = settings.get("hooks")
    hooks = hooks if isinstance(hooks, dict) else {}

    for event, subcmd in retired.items():
        commands = [
            h.get("command")
            for g in hooks.get(event, []) or [] if isinstance(g, dict)
            for h in g.get("hooks", []) or [] if isinstance(h, dict)
        ]
        check(
            f"retired hook {event} -> mnemo {subcmd} is NOT generated",
            not any(
                isinstance(c, str) and "mnemo" in c and c.split()[-1:] == [subcmd]
                for c in commands
            ),
            detail=str(commands),
        )

    for event, (subcmd, matcher) in expected.items():
        groups = [g for g in hooks.get(event, []) or [] if isinstance(g, dict)]
        commands = [
            h.get("command")
            for g in groups
            for h in g.get("hooks", []) or []
            if isinstance(h, dict)
        ]
        ours = [
            c for c in commands
            if isinstance(c, str) and "mnemo" in c and c.split()[-1:] == [subcmd]
        ]
        check(
            f"hook {event} runs mnemo {subcmd}",
            len(ours) == 1,
            detail=str(commands),
        )
        if matcher is not None:
            check(
                f"hook {event} matches {matcher}",
                any(g.get("matcher") == matcher for g in groups),
                detail=str([g.get("matcher") for g in groups]),
            )
        check(
            f"hook {event} command carries no machine-specific path",
            all(
                str(Path.home()) not in c
                and Path.home().as_posix() not in c
                and _WIN_DRIVE_RE.search(c) is None
                for c in ours
            ),
            detail=str(ours),
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
        # A default `init` wires NO hook (design #15): nothing to plan, so the
        # file is not even rewritten. Asserted before anything else, because a
        # regression here puts a hook into somebody's git-tracked settings.
        default_settings_text = _plan_settings(settings_path, [])
        # The seeded shape is what the rest of the hook checks look at.
        settings_text = _plan_settings(settings_path, [], False,
                                       frozenset({"memory", "inject"}))
        mcp = json.loads(mcp_text or "{}")
        settings = json.loads(settings_text or "{}")

        entry = mcp.get("mcpServers", {}).get("mnemo")
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
        check(
            "foreign settings preserved",
            settings.get("permissions") == {"allow": ["Read"]},
            detail=str(settings),
        )
        check(
            "default init plans no hook at all",
            default_settings_text is None,
            detail=str(default_settings_text),
        )
        _check_hook_wiring(settings)

        mcp_path.write_text(mcp_text or "", encoding="utf-8")
        settings_path.write_text(settings_text or "", encoding="utf-8")
        check("MCP idempotent",
              _plan_mcp(mcp_path, [], token=_FAKE_TOKEN) is None)
        # A rotated token is not a conflict — it is the same entry with a new
        # credential, and `init` must re-issue it rather than refuse or, worse,
        # report "already present" and leave wiring that no longer opens.
        rotated = _plan_mcp(mcp_path, [], token="b" * 48)
        check(
            "a rotated bank token is rewritten, not refused",
            rotated is not None
            and "b" * 48 in json.loads(rotated)["mcpServers"]["mnemo"]["url"],
            detail=str(rotated),
        )
        check(
            "hooks idempotent",
            _plan_settings(settings_path, [], False,
                           frozenset({"memory", "inject"})) is None,
        )
        # A seeded project is not un-seeded by a plain re-run: `init` without
        # the flags must leave an already-wired hook alone, never remove it
        # behind the user's back. Removal is `--migrate`, and only that.
        check(
            "plain re-run does not unwire an existing seed",
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
            return json.loads(text or "{}").get("mcpServers", {}).get("mnemo", {})

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
        check("mnemo entry lands in the template", "mnemo" in entry,
              detail=str(sorted(entry)))
        check("foreign template entry preserved", "chrome" in entry,
              detail=str(sorted(entry)))
        _check_mcp_shape(entry.get("mnemo", {}), placeholders=True)

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
                 .get("mcpServers", {}).get("mnemo", {}))
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
        entry = json.loads(text or "{}").get("mcpServers", {}).get("mnemo", {})
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
                 .get("mcpServers", {}).get("mnemo", {}))
        _check_mcp_shape(entry, placeholders=True)
        # Nothing to add and nothing it is allowed to remove, so it plans no
        # write to either file at all — and MNEMO_BANK is still on disk.
        check("a plain init does NOT touch .mcp.env or mcp-setup.sh",
              ".mcp.env" not in plain and "mcp-setup.sh" not in plain,
              detail=str(sorted(plain)))
        check("MNEMO_BANK survives a plain init",
              "MNEMO_BANK" in _read(proj2 / ".mcp.env")
              and "{{MNEMO_BANK}}" in _read(proj2 / "mcp-setup.sh"))

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
        check(".mcp.json is written when there is no template",
              ".mcp.json" in written, detail=str(sorted(written)))

        text = written.get(".gitignore", "")
        check(".gitignore gains .mcp.json",
              any(line.strip() == ".mcp.json" for line in text.splitlines()),
              detail=text)
        # Exactly one entry added, and the existing lines untouched in place:
        # a .gitignore is human-curated and git-tracked, so the smallest
        # possible edit is the only acceptable one.
        added = [l for l in text.splitlines()
                 if l.strip() and not l.startswith("#")
                 and l not in ("venv/", "*.pyc")]
        check("no other .gitignore line is added or reordered",
              added == [".mcp.json"]
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
        check("init writes into an untracked .mcp.json",
              any(p.name == ".mcp.json" for p, _ in wiring.writes),
              detail=str([p.name for p, _ in wiring.writes]))

        # Now track it, and the same call must refuse.
        _write(proj / ".mcp.json", '{"mcpServers": {}}\n')
        git(proj, "add", "-f", ".mcp.json")
        check("a tracked file reads as tracked",
              _git_tracked(proj, ".mcp.json") is True)

        for label, migrate in (("init", False), ("--migrate", True)):
            try:
                _plan_wiring(proj, token=_FAKE_TOKEN, migrate=migrate)
                refused, message = False, ""
            except _Refuse as exc:
                refused, message = True, str(exc)
            check(f"{label} REFUSES a tracked .mcp.json", refused)
            check(f"{label}'s refusal names `git rm --cached`",
                  "git rm --cached .mcp.json" in message,
                  detail=message[:200])
            check(f"{label}'s refusal leaks no token",
                  _FAKE_TOKEN not in message, detail=message[:200])

        # The template branch has its own file that gets a literal token.
        _write(proj / ".mcp.json.template", '{"mcpServers": {}}\n')
        _write(proj / ".mcp.env", "MNEMO_PORT=8918\n")
        git(proj, "add", "-f", ".mcp.env")
        try:
            _plan_wiring(proj, token=_FAKE_TOKEN, migrate=False)
            refused, message = False, ""
        except _Refuse as exc:
            refused, message = True, str(exc)
        check("a tracked .mcp.env is refused too", refused, detail=message[:160])
        check("that refusal names `git rm --cached .mcp.env`",
              "git rm --cached .mcp.env" in message, detail=message[:200])


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

        walk = scan_bank(config.resolve(root).root)
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
        walk = scan_bank(config.resolve(root).root)
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
            and all(walk[v].abs_path == root / v for v in identifiers),
            detail=str(identifiers),
        )
        # `scan_bank` sorts Path objects, so key order is normcase-folded on
        # Windows and byte-ordered on POSIX — the two platforms disagree.
        # What the indexer actually relies on is that ONE machine repeats
        # itself, so assert that, not a cross-platform order.
        check(
            "walk order is stable across calls",
            list(scan_bank(config.resolve(root).root)) == list(walk),
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


def main() -> int:
    test_scaffold()
    test_scaffold_template_project()
    test_scaffold_drops_the_bank_segment()
    test_scaffold_hand_edited_sed_line()
    test_scaffold_gitignore()
    test_scaffold_refuses_a_tracked_file()
    test_git_index_probe()
    test_project_resolution()
    test_index_paths()
    test_model_cache_validation()
    print(
        f"\n{_passed} passed, {_failed} failed, "
        f"{_xfailed} xfailed (awaiting a later phase), {_xpassed} xpassed"
    )
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
