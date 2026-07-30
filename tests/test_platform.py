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
    _plan_mcp,
    _plan_settings,
)

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


def _check_mcp_shape(entry: dict) -> None:
    """Contracts §10.4 — the HTTP wiring, now that phase 4 has landed.

    These were written as xchecks against the target shape while MCP was
    still stdio, and they turned green the moment the switch arrived. They
    are promoted to real checks: a target that has been reached is a
    regression guard, and leaving it marked "awaiting a later phase" makes
    the suite lie about what it covers.
    """
    check("MCP transport is http", entry.get("type") == "http",
          detail=str(entry.get("type")))

    url = entry.get("url")
    check(
        "MCP url is loopback",
        isinstance(url, str)
        and re.fullmatch(r"http://(127\.0\.0\.1|localhost|\[::1\]):\d+/\S*", url)
        is not None,
        detail=str(url),
    )

    headers = entry.get("headers")
    headers = headers if isinstance(headers, dict) else {}

    auth = headers.get("Authorization")
    check(
        "Authorization is the ${MNEMO_API_TOKEN} placeholder",
        auth == "Bearer ${MNEMO_API_TOKEN}",
        detail=str(auth),
    )
    # The placeholder must survive verbatim: a resolved token in a
    # git-tracked file is the failure this whole block exists to prevent.
    check(
        "Authorization holds no literal token",
        isinstance(auth, str)
        and "${MNEMO_API_TOKEN}" in auth
        and _SECRET_RE.search(auth) is None,
        detail=str(auth),
    )

    bank = headers.get("X-Mnemo-Bank")
    check(
        "X-Mnemo-Bank is present and non-empty",
        isinstance(bank, str) and bool(bank.strip()),
        detail=str(bank),
    )
    # The one machine-readable guard against a machine-derived value in git:
    # bank_id is sha1(abs path)[:16] and points nowhere after a clone.
    check(
        "X-Mnemo-Bank is a name, not a bank_id",
        isinstance(bank, str)
        and bool(bank.strip())
        and _BANK_ID_RE.fullmatch(bank.strip()) is None,
        detail=str(bank),
    )


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

        mcp_text = _plan_mcp(mcp_path, [])
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

        # Generation-agnostic invariants: true of the stdio form today and
        # of the HTTP form after phase 4. Nothing machine-specific and no
        # credential may ever land in this git-tracked file.
        home = Path.home()
        check(
            "MCP entry carries no machine-specific path",
            str(home) not in entry_text
            and home.as_posix() not in entry_text
            and _WIN_DRIVE_RE.search(entry_text) is None,
            detail=entry_text,
        )
        check(
            "MCP entry carries no literal secret",
            _SECRET_RE.search(entry_text) is None,
            detail=str(_SECRET_RE.findall(entry_text)),
        )
        check(
            "MCP entry carries no bank_id",
            _BANK_ID_RE.search(entry_text) is None,
            detail=entry_text,
        )

        _check_mcp_shape(entry if isinstance(entry, dict) else {})

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
        check("MCP idempotent", _plan_mcp(mcp_path, []) is None)
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
                _plan_mcp(mcp_path, [])
            except _Refuse:
                return True
            return False

        check("legacy L1 (/bin/sh wrapper) is an explicit conflict", refuses(l1))
        # L2 is still the LIVE value today, so plain `init` correctly treats
        # it as already-wired. It becomes a conflict only once phase 4 makes
        # the HTTP form canonical.
        check(
            "legacy L2 (stdio ${HOME} launcher) is an explicit conflict",
            refuses(l2),
        )


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
