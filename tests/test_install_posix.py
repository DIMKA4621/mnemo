"""POSIX installer smoke test (no model download).

The mirror image of `test_install_windows.py`, and it exists for the same
reason that one does: an installer is only proven by being run. Until this
file, `install.sh` and `uninstall.sh` had never been executed by anything,
anywhere -- CI installed the dependencies straight into the runner and then
tested the engine's internals, so the two shell scripts were shipped on the
strength of a reading.

Everything happens inside a throwaway `--home`, which is what makes it safe
to run on a developer's own machine: an isolated home writes no profile
block, registers no autostart unit, downloads no model and starts no
service. The last three checks assert exactly that, because "it did not
reach out and touch the machine" is the property that lets this run at all.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

# A runner's locale is not always UTF-8, and the engine home below is
# deliberately Cyrillic -- so the installer's echo of its own path would
# raise instead of printing. Same guard as the other two suites.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install.sh"
UNINSTALLER = REPO / "uninstall.sh"

_passed = 0


def ok(name: str) -> None:
    """Count a check as it passes — the tally is measured, not asserted."""
    global _passed
    _passed += 1
    print(f"PASS  {name}")


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    expect: int = 0,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1200,
    )
    if result.returncode != expect:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(
            f"command exited {result.returncode}, expected {expect}: "
            f"{' '.join(command)}"
        )
    return result


def report(text: str, prefix: str, width: int) -> dict[str, str]:
    """The installers' aligned report lines, as {label: value}.

    Both scripts print through a single `line()` helper whose format string
    pads the label to a fixed width, so the split is exact rather than a
    guess at how many spaces there were.
    """
    head = f"{prefix}:   "
    found: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.startswith(head):
            continue
        body = raw[len(head):]
        found[body[:width].strip()] = body[width:].strip()
    return found


def install_report(text: str) -> dict[str, str]:
    return report(text, "install.sh", 13)


def uninstall_report(text: str) -> dict[str, str]:
    return report(text, "uninstall.sh", 15)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "absent"


def user_scope() -> tuple[str, ...]:
    """Everything an isolated run must leave exactly as it found it.

    Not "no profile block exists" — once this machine has a real engine, one
    legitimately does. The invariant is narrower: whatever is there must be
    unchanged, and only a before-value can show that.
    """
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    return (
        digest(home / ".profile"),
        digest(home / ".bashrc"),
        digest(config / "systemd" / "user" / "mnemo.service"),
        digest(home / "Library" / "LaunchAgents" / "dev.mnemo.service.plist"),
    )


def test_heartbeat_spinner() -> None:
    """`run_with_heartbeat`'s spinner (2026-08-23): the old dot-per-second
    indicator that only ever grew rightward is gone, replaced by an
    in-place spinner (| / - \\) that redraws itself with a backspace byte
    -- and a near-instant command must not have that backspace eat a
    character off the label itself. Isolated via the new source-guard
    (`[ "${BASH_SOURCE[0]}" != "${0}" ] && return`, added alongside the
    spinner specifically so this is testable without a real install), the
    same isolation technique `check_script_encoding`'s dot-sourcing already
    uses on the Windows side.
    """
    slow = run(["bash", "-c", f"source {shlex.quote(str(INSTALLER))}; run_with_heartbeat 'slow label' sleep 2"])
    assert "\x08" in slow.stdout, (
        "no backspace byte in heartbeat output -- still the old growing-dots indicator?"
    )
    ok("the spinner redraws in place via a backspace byte, not growing dots")

    # `true` is as close to instant as a real child process gets on POSIX --
    # close enough to stand in for the "zero frames printed" edge case
    # install.ps1's own $spinnerPrinted guard exists for. Either way (zero
    # frames, or one frame the matching backspace correctly erases), the
    # label text itself must survive completely intact.
    instant = run(["bash", "-c", f"source {shlex.quote(str(INSTALLER))}; run_with_heartbeat 'instant label' true"])
    assert "install.sh: instant label" in instant.stdout, instant.stdout
    assert "done" in instant.stdout, instant.stdout
    ok("a near-instant command does not corrupt the label with a stray backspace")


def test_heartbeat_failure_path() -> None:
    """`run_with_heartbeat`'s failure path (2026-08-23, found by the tester
    and reproduced independently): `wait "$heartbeat_pid"; heartbeat_code=$?`
    as a bare statement under `set -euo pipefail` aborts the whole shell AT
    `wait` the instant the backgrounded child exits nonzero -- before
    `heartbeat_code=$?` is ever read. That made the entire failure path
    (the " done" print, the `cat "$heartbeat_log" >&2` dump, the `rm -f`
    cleanup) dead code on every real failure, silently swallowing exactly
    the output the ticket dropped `--quiet` to preserve. Fixed with
    `heartbeat_code=0; wait "$heartbeat_pid" || heartbeat_code=$?`, which
    puts `wait` in a conditional context `set -e` exempts.
    """
    failing = run(
        [
            "bash", "-c",
            f"set -euo pipefail; source {shlex.quote(str(INSTALLER))}; "
            "echo BEFORE; "
            "run_with_heartbeat 'FAILLABEL' bash -c 'echo oops-marker >&2; exit 7'; "
            "echo AFTER",
        ],
        expect=7,
    )
    assert "BEFORE" in failing.stdout, failing.stdout
    assert "install.sh: FAILLABEL" in failing.stdout, failing.stdout
    assert " done" in failing.stdout, (
        "run_with_heartbeat's own failure path never ran -- still aborting at `wait`?",
        failing.stdout,
    )
    assert "oops-marker" in failing.stderr, (
        "the failure-log dump (cat \"$heartbeat_log\" >&2) never ran",
        failing.stderr,
    )
    ok("run_with_heartbeat prints its failure dump instead of dying silently at `wait`")

    # `set -e` still (correctly) aborts the CALLER right after
    # run_with_heartbeat returns 7 as a bare statement, same as an
    # unwrapped failing command in that position would -- the fix restores
    # the documented contract ("callers under set -e behave exactly as an
    # unwrapped call would"), it does not swallow the failure.
    assert "AFTER" not in failing.stdout, failing.stdout
    ok("set -e still aborts the caller after run_with_heartbeat returns its real exit code")


def check_no_quiet_on_requirements_install() -> None:
    """`--quiet` dropped from both pip-install-requirements call sites
    (2026-08-23, mirroring install.ps1's own 2026-08-22 fix) so a failure
    dump is actually useful -- confirmed nothing parses this output
    programmatically here, unlike install.ps1's -ProgressFile mechanism.
    """
    text = INSTALLER.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if "requirements.txt" in line and "--quiet" in line
    ]
    assert not offenders, f"--quiet still present on a requirements.txt pip install: {offenders}"
    ok("install.sh drops --quiet from both requirements.txt pip installs")


def main() -> int:
    if os.name == "nt":
        print("SKIP  POSIX installer test")
        return 0

    # The scripts are documented as `./install.sh` and `./uninstall.sh`, and
    # that only works if git carries the executable bit. It is checked here
    # rather than assumed because the bit is invisible in a diff: uninstall.sh
    # shipped as 100644 and would have died with "Permission denied" on the
    # very first line a user typed.
    for script in (INSTALLER, UNINSTALLER):
        assert os.access(script, os.X_OK), f"{script.name} is not executable"
        ok(f"{script.name} carries the executable bit")

    test_heartbeat_spinner()
    test_heartbeat_failure_path()
    check_no_quiet_on_requirements_install()

    help_text = run([str(INSTALLER), "--help"])
    for flag in ("--check", "--deps-only", "--no-model", "--home DIR"):
        assert flag in help_text.stdout, (flag, help_text.stdout)
    ok("--help prints the flag list from the header block")

    bad = run([str(INSTALLER), "--nope"], expect=2)
    assert "unknown argument: --nope" in bad.stderr, bad.stderr
    ok("an unknown flag exits 2 without doing anything")

    with tempfile.TemporaryDirectory(prefix="mnemo install ") as raw:
        engine = Path(raw) / "рушій з пробілами"

        # --check must work against a home that does not exist yet: it is the
        # first thing anyone runs when they are not sure what they have.
        absent = install_report(
            run([str(INSTALLER), "--check", "--home", str(engine)]).stdout
        )
        assert absent["home dir"] == "MISSING", absent
        assert absent["launcher"] == "MISSING", absent
        assert absent["banks"] == "none registered", absent
        ok("--check reports an absent engine and changes nothing")
        assert not engine.exists(), "--check created the engine home"

        install = [str(INSTALLER), "--home", str(engine)]

        first = run(install)
        launcher = engine / "bin" / "mnemo"
        # src/ and .venv live under the versioned tree (versions/local/);
        # `current` is the symlink install.sh's `ln -sfn` repoints (bug C
        # fix) -- the POSIX mirror of install.ps1's `current` junction.
        venv_python = engine / "current" / ".venv" / "bin" / "python"
        assert launcher.is_file(), launcher
        assert os.access(launcher, os.X_OK), "the launcher is not executable"
        assert venv_python.exists(), venv_python
        ok("fresh install into a path with spaces and Cyrillic")

        # The venv-creation step (2026-08-23) used to be silent; it must now
        # have gone through run_with_heartbeat with its own Label, not just
        # exist as a function nothing calls.
        assert "install.sh: creating the virtual environment" in first.stdout, first.stdout
        ok("venv creation went through the heartbeat wrapper")

        # A default install warms the model, starts the service and runs
        # doctor. An isolated --home must do NONE of that: it is a manual or
        # test copy, and this suite is exactly why. A 2.2 GB download, a
        # second process claiming port 8918, or a `doctor` reporting the real
        # engine would each be a test reaching out to touch the machine.
        assert "isolated home: skipped the model, the service and the check" \
            in first.stdout, first.stdout
        assert "verifying --" not in first.stdout
        # The banner is a claim; this is the behaviour. "The model is
        # downloaded only by an explicit warmup" is a binding invariant, so
        # assert the cache is genuinely bare rather than trusting the text.
        cache_dir = engine / "model-cache"
        stray = [p for p in cache_dir.rglob("*") if p.is_file()] if cache_dir.exists() else []
        assert not stray, f"install downloaded model files: {stray[:5]}"
        assert not (engine / "state" / "service.pid").exists()
        assert not (engine / "state" / "service.json").exists()
        ok("isolated install downloads no model and starts nothing")

        # The prompt must never fire without a terminal — a unit or a piped
        # run would otherwise hang forever, or read one byte of the caller's
        # data as the answer.
        assert "download it now?" not in first.stdout
        ok("no interactive prompt in a non-interactive run")

        checked = install_report(run(install + ["--check"]).stdout)
        assert checked["python deps"] == "present", checked
        assert checked["launcher"] == "present", checked
        assert checked["engine code"] == "present", checked
        assert checked["service"] == "stopped", checked
        ok("read-only installer check")

        # The question that actually matters, and the one whose absence let a
        # macOS build ship that imported sqlite_vec happily and then could not
        # open a single bank.
        assert checked["sqlite-vec"] == "loadable", checked
        ok("this Python can load SQLite extensions")

        # `python -c` puts the caller's cwd on sys.path before anything else,
        # so a target project with its own top-level `src` package would
        # shadow the engine's. The launcher inserts the engine home in front.
        shadow = Path(raw) / "target project" / "src"
        shadow.mkdir(parents=True)
        (shadow / "__init__.py").write_text("", encoding="utf-8")
        (shadow / "cli.py").write_text(
            'raise RuntimeError("project src shadowed mnemo")\n',
            encoding="utf-8",
        )
        helped = run([str(launcher), "--help"], cwd=shadow.parent)
        assert "Project memory" in helped.stdout, helped.stdout
        ok("launcher ignores a project-local src package")

        state_sentinel = engine / "state" / "keep.txt"
        cache_sentinel = engine / "model-cache" / "keep.txt"
        state_sentinel.write_text("state", encoding="utf-8")
        cache_sentinel.write_text("cache", encoding="utf-8")

        run(install)
        assert state_sentinel.read_text(encoding="utf-8") == "state"
        assert cache_sentinel.read_text(encoding="utf-8") == "cache"
        ok("reinstall preserves state and model cache")

        # --deps-only must refresh the venv without re-mirroring src/, so it
        # is safe to run while the repository's engine code is mid-refactor.
        # src/ lives under `current` (-> versions/local), not the engine root.
        in_flight = engine / "current" / "src" / "in flight.py"
        in_flight.write_text("# uncommitted engine work\n", encoding="utf-8")
        deps_only = run(install + ["--deps-only"])
        assert "deps-only" in deps_only.stdout, deps_only.stdout
        assert in_flight.is_file(), "--deps-only re-mirrored src/"
        assert state_sentinel.read_text(encoding="utf-8") == "state"
        run([str(launcher), "--help"])
        ok("--deps-only refreshes packages only")

        # A v2 engine, recognised by what v2 never had: a banks registry.
        # Every v2 index is orphaned the instant v3 runs -- v2 keyed the file
        # by PROJECT root, v3 by BANK root -- and no v2 database carries a
        # `meta` table, so the path cannot be recovered from inside either.
        # Nothing will ever open them again, so the installer retires them.
        assert not (engine / "state" / "banks.json").exists()
        legacy = [engine / "state" / f"ab12cd34{n:08x}.db" for n in range(3)]
        for path in legacy:
            path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 512)
        stray_wal = legacy[0].with_name(legacy[0].name + "-wal")
        stray_wal.write_bytes(b"\x00" * 32)

        upgraded = run(install)
        assert "found a v2 engine: 3 index file(s)" in upgraded.stdout, upgraded.stdout
        ok("installer recognises a v2 engine by its missing registry")

        assert not any(p.exists() for p in legacy), \
            [str(p) for p in legacy if p.exists()]
        ok("v2 indexes are retired by the upgrade")

        assert not stray_wal.exists()
        ok("a v2 index takes its -wal sibling with it")

        assert state_sentinel.read_text(encoding="utf-8") == "state"
        assert cache_sentinel.read_text(encoding="utf-8") == "cache"
        ok("retiring v2 indexes spares everything else in state/")

        # The same full install proves the mirror is a mirror: the file left
        # behind by --deps-only belongs to no commit, so it must be gone.
        # rsync --delete and the cp fallback have to agree about that.
        assert not in_flight.exists(), "a full install kept a stray src/ file"
        ok("a full install mirrors src/ rather than merging into it")

        # And v2 detection must be a one-time event: with the indexes gone
        # there is nothing left to find.
        quiet = run(install)
        assert "found a v2 engine" not in quiet.stdout, quiet.stdout
        ok("a clean engine reports no v2 leftovers")

        assert "isolated home: skipped token export, profile and autostart" \
            in first.stdout, first.stdout
        ok("isolated install skips every user-scope registration")

        # ---- uninstall, against the same isolated engine -----------------
        # Last, because it destroys what every check above needs. The user
        # scope is captured here rather than at the top: the installs above
        # are the thing that must not have touched it, and the uninstalls
        # below must not either.
        before_scope = user_scope()

        uninstall = [str(UNINSTALLER), "--home", str(engine)]

        dry = run(uninstall + ["--dry-run"])
        assert "dry run: nothing was removed." in dry.stdout, dry.stdout
        assert "isolated home:" in dry.stdout, dry.stdout
        assert launcher.is_file(), "a dry run deleted the engine"
        survey = uninstall_report(dry.stdout)
        # uninstall.sh's survey line now checks `current/src/cli.py` (fixed
        # in e74fcb0 to match uninstall.ps1's own current/versions-aware
        # text), so it reports this suffixed form right after a real install.
        assert survey["engine code"] == "src/, launcher, requirements (versions/, current)", survey
        assert survey["service"] == "not running", survey
        # An isolated home must not even *offer* to remove the machine-level
        # registrations: listing them would be a promise the removal step
        # correctly refuses to keep.
        assert "autostart" not in survey, survey
        assert "profile block" not in survey, survey
        ok("uninstall --dry-run reports and changes nothing")

        # No terminal and no --yes: a prompt nobody can see would hang or read
        # one byte of the caller's data as consent. For a delete, refusing is
        # the only safe reading -- and the engine must survive it intact.
        unattended = run(uninstall)
        assert "non-interactive run and no --yes" in unattended.stdout
        assert launcher.is_file(), "an unconfirmed uninstall deleted the engine"
        ok("uninstall refuses to delete without a terminal or --yes")

        kept = run(uninstall + ["--keep-model", "--keep-state", "--yes"])
        # Engine code + venv live under versions/local/, referenced through
        # the `current` symlink -- both must go; state/ and model-cache/
        # (checked below) must not.
        assert not (engine / "versions").exists(), kept.stdout
        assert not (engine / "current").exists(), kept.stdout
        assert not venv_python.exists(), kept.stdout
        assert not launcher.exists(), kept.stdout
        assert state_sentinel.read_text(encoding="utf-8") == "state"
        assert cache_sentinel.read_text(encoding="utf-8") == "cache"
        ok("--keep-model --keep-state removes the engine but not the data")

        final = run(uninstall + ["--yes"])
        assert not engine.exists(), final.stdout
        assert "mnemo is gone from this machine." in final.stdout
        ok("full uninstall removes the engine home")

        absent_run = run(uninstall + ["--yes"])
        assert "no engine installed there" in absent_run.stdout, absent_run.stdout
        ok("uninstalling twice is not an error")

        assert user_scope() == before_scope, (
            "an isolated run reached into user scope"
        )
        ok("isolated runs left the profile and autostart untouched")

    print(f"\n{_passed} passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
