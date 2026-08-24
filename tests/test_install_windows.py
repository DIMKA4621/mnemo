"""Native Windows installer smoke test (no model download)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The engine is installed into a Cyrillic path on purpose, so the installer's
# own output carries Cyrillic — and printing it on a cp1252 console raises
# instead of printing. A developer console is UTF-8 and never showed this; a
# CI runner's is not. Same guard as test_platform.py, same reason.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install.ps1"
UNINSTALLER = REPO / "uninstall.ps1"

_passed = 0


def ok(name: str) -> None:
    """Count a check as it passes — the tally is measured, not asserted."""
    global _passed
    _passed += 1
    print(f"PASS  {name}")


def run(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}"
        )
    return result


def check_script_encoding(script: Path) -> None:
    """A shipped .ps1 must stay pure ASCII, and must parse.

    Windows PowerShell 5.1 decodes a BOM-less .ps1 as the system ANSI
    codepage, so a single em dash in a double-quoted string breaks the
    quoting and the whole script fails to parse. That happened; this is
    the guard, and it covers the uninstaller for the same reason.
    """
    text = script.read_text(encoding="utf-8")
    offenders = [
        (number, line.strip())
        for number, line in enumerate(text.splitlines(), start=1)
        if any(ord(char) > 127 for char in line)
    ]
    assert not offenders, f"non-ASCII in {script.name}: {offenders[:3]}"
    ok(f"{script.name} is pure ASCII (PowerShell 5.1 reads it as ANSI)")

    parse = run([
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "$errors = $null; "
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{script}',"
        " [ref]$null, [ref]$errors); "
        "if ($errors -and $errors.Count -gt 0) { "
        "$errors | ForEach-Object { Write-Host $_.Message }; exit 1 } "
        "else { Write-Host 'parses cleanly' }",
    ])
    assert "parses cleanly" in parse.stdout, parse.stdout
    ok(f"{script.name} parses with zero errors")


def test_heartbeat_stall_detection() -> None:
    """`Invoke-CheckedWithHeartbeat`'s stall detector (2026-08-22): "still
    alive" and "still making progress" are different claims — a child that
    keeps printing, however slowly, must run to completion; one that goes
    genuinely silent must be killed and reported well before its own
    process would ever exit on its own, and well before any outer ceiling.
    Isolated `.ps1` harness (dot-source + direct call), same pattern
    `check_script_encoding` already uses for exercising this file's
    functions without a full install.
    """
    with tempfile.TemporaryDirectory(prefix="mnemo heartbeat ") as raw:
        work = Path(raw)

        trickle = work / "trickle.py"
        trickle.write_text(
            "import sys, time\n"
            "for i in range(4):\n"
            "    time.sleep(0.5)\n"
            "    print('line', i)\n"
            "    sys.stdout.flush()\n",
            encoding="utf-8",
        )
        stall = work / "stall.py"
        stall.write_text(
            "import sys, time\n"
            "print('one line, then silence')\n"
            "sys.stdout.flush()\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )

        trickle_cmd = (
            f". '{INSTALLER}'; "
            f"Invoke-CheckedWithHeartbeat '{sys.executable}' @('{trickle}') "
            "'test trickle' 'trickle failed' -StallTimeoutSec 2; "
            "Write-Output 'MNEMO_TEST_TRICKLE_OK'"
        )
        trickle_result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", trickle_cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        assert trickle_result.returncode == 0, trickle_result.stdout + trickle_result.stderr
        assert "MNEMO_TEST_TRICKLE_OK" in trickle_result.stdout
        ok("a slow-but-alive child (output every 0.5s) is never treated as stalled")

        # The old indicator (a dot appended once a second, never erased) is
        # gone; the new one is an in-place spinner (| / - \) that redraws
        # itself with a backspace byte. A ~2s run at a ~130ms tick prints
        # several frames, so the byte must show up in the captured output.
        assert "\x08" in trickle_result.stdout, (
            "no backspace byte in heartbeat output -- still the old growing-dots indicator?"
        )
        ok("the spinner redraws in place via a backspace byte, not growing dots")

        stall_cmd = (
            f". '{INSTALLER}'; "
            f"Invoke-CheckedWithHeartbeat '{sys.executable}' @('{stall}') "
            "'test stall' 'stall failed' -StallTimeoutSec 2"
        )
        started = time.monotonic()
        stall_result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", stall_cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        elapsed = time.monotonic() - started
        combined = stall_result.stdout + stall_result.stderr
        assert stall_result.returncode != 0, combined
        assert "looks stalled, not just slow" in combined, combined
        ok('a genuinely silent child throws the "looks stalled" error')
        assert elapsed < 15, f"took {elapsed:.1f}s -- the stalled child was not actually killed"
        ok("the stalled child is killed promptly, not waited out to its own 30s sleep")


def test_heartbeat_zero_tick_edge_case() -> None:
    """A child that exits before the heartbeat loop's `while (-not
    $proc.HasExited)` is ever first evaluated (an instant command) must
    print zero spinner frames -- and the unconditional backspace that used
    to precede " done" would then eat a character off the LABEL itself
    instead of a spinner frame it never drew. Found live on a real
    self-update with a warm pip cache (2026-08-22); `$spinnerPrinted` is
    the guard. `python -c "pass"` is as close to instant as a real child
    process gets -- close enough that this is also a reasonable proxy for
    the "zero frames" case even where interpreter startup itself takes a
    tick or two: either way, the label and " done (" must survive intact.
    """
    label = "MNEMO_TEST_ZEROTICK_LABEL"
    cmd = (
        f". '{INSTALLER}'; "
        f"Invoke-CheckedWithHeartbeat '{sys.executable}' @('-c', 'pass') "
        f"'{label}' 'zerotick failed'"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # A corrupted label (one character eaten by a stray backspace) would no
    # longer match this substring at all -- this is not a cosmetic check.
    assert f"install.ps1: {label}" in result.stdout, result.stdout
    assert "done (" in result.stdout, result.stdout
    ok("a near-instant child does not corrupt the label with a stray backspace")


def _run_ps(command: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Bare `powershell.exe -Command`, no returncode assertion -- the two
    disk-space checks below need to inspect a thrown exception's message
    themselves, unlike `run()` above which always expects a clean exit.
    """
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def test_disk_space_check() -> None:
    """Get-RequiredDiskBytes / Get-AvailableDiskBytes / Test-DiskSpace
    (MN-21, 2026-08-24): isolated dot-source harness, same pattern
    `check_script_encoding` and `test_heartbeat_stall_detection` already use
    for exercising this file's functions without a full install. Split into
    a "get the number" half and a "decide + throw" half specifically so
    Get-AvailableDiskBytes can be stubbed here instead of requiring an
    actually-full disk.
    """
    with tempfile.TemporaryDirectory(prefix="mnemo disk ") as raw:
        target = Path(raw) / "engine-home"
        target.mkdir()

        no_cache = _run_ps(f". '{INSTALLER}'; Write-Output (Get-RequiredDiskBytes '{target}' $false)")
        assert no_cache.returncode == 0, no_cache.stdout + no_cache.stderr
        assert no_cache.stdout.strip() == str(300_000_000 + 2_200_000_000 + 500_000_000), no_cache.stdout
        ok("Get-RequiredDiskBytes counts the model when the cache is empty")

        no_model = _run_ps(f". '{INSTALLER}'; Write-Output (Get-RequiredDiskBytes '{target}' $true)")
        assert no_model.returncode == 0, no_model.stdout + no_model.stderr
        assert no_model.stdout.strip() == str(300_000_000 + 500_000_000), no_model.stdout
        ok("Get-RequiredDiskBytes drops the model when SkipModel is set")

        model_cache = target / "model-cache"
        model_cache.mkdir()
        (model_cache / "file.bin").write_bytes(b"x")
        cached = _run_ps(f". '{INSTALLER}'; Write-Output (Get-RequiredDiskBytes '{target}' $false)")
        assert cached.returncode == 0, cached.stdout + cached.stderr
        assert cached.stdout.strip() == str(300_000_000 + 500_000_000), cached.stdout
        ok("Get-RequiredDiskBytes drops the model when the cache already looks populated")

        low = _run_ps(
            f". '{INSTALLER}'; "
            "function Get-AvailableDiskBytes { param([string]$EngineHome) return [long]1000 }; "
            "try { Test-DiskSpace 'C:\\nonexistent\\target'; Write-Output 'DID_NOT_THROW' } "
            "catch { Write-Output ('THREW:' + $_.Exception.Message) }"
        )
        assert low.returncode == 0, low.stdout + low.stderr
        assert "DID_NOT_THROW" not in low.stdout, low.stdout
        assert "not enough free disk space" in low.stdout, low.stdout
        assert "required:" in low.stdout, low.stdout
        assert "available:" in low.stdout, low.stdout
        assert "free up space, then re-run." in low.stdout, low.stdout
        ok("Test-DiskSpace throws with the disk-space report when available < required")

        high = _run_ps(
            f". '{INSTALLER}'; "
            "function Get-AvailableDiskBytes { param([string]$EngineHome) return [long]99999999999 }; "
            "try { Test-DiskSpace 'C:\\nonexistent\\target'; Write-Output 'DID_NOT_THROW' } "
            "catch { Write-Output ('THREW:' + $_.Exception.Message) }"
        )
        assert high.returncode == 0, high.stdout + high.stderr
        assert "DID_NOT_THROW" in high.stdout, high.stdout
        ok("Test-DiskSpace does not throw when available >= required")


def check_disk_space_constants_agree() -> None:
    """The three disk-space budget numbers are hard-coded independently in
    install.sh, install.ps1 and src/config.py -- bash/PowerShell cannot
    import a Python module's constants, so each carries its own literal
    (see config.py's own comment on ENGINE_VERSION_SIZE_BYTES). This is what
    keeps the three from silently drifting apart. Duplicated in
    test_install_posix.py rather than shared, so this coverage does not
    depend on which OS's CI happens to run -- both installer suites already
    self-skip on the other platform.
    """
    sh_text = (REPO / "install.sh").read_text(encoding="utf-8")
    ps1_text = INSTALLER.read_text(encoding="utf-8")
    py_text = (REPO / "src" / "config.py").read_text(encoding="utf-8")

    def grab(pattern: str, text: str) -> int:
        match = re.search(pattern, text)
        assert match, f"pattern not found: {pattern}"
        return int(match.group(1).replace("_", ""))

    triples = [
        (
            "engine version size",
            grab(r"ENGINE_VERSION_BYTES=(\d+)", sh_text),
            grab(r"\$EngineVersionBytes = (\d+)", ps1_text),
            grab(r"ENGINE_VERSION_SIZE_BYTES: int = ([\d_]+)", py_text),
        ),
        (
            "model download size",
            grab(r"MODEL_BYTES=(\d+)", sh_text),
            grab(r"\$ModelBytes = (\d+)", ps1_text),
            grab(r"MODEL_DOWNLOAD_SIZE_BYTES: int = ([\d_]+)", py_text),
        ),
        (
            "disk buffer",
            grab(r"DISK_BUFFER_BYTES=(\d+)", sh_text),
            grab(r"\$DiskBufferBytes = (\d+)", ps1_text),
            grab(r"INSTALL_DISK_BUFFER_BYTES: int = ([\d_]+)", py_text),
        ),
    ]
    for name, sh_val, ps1_val, py_val in triples:
        assert sh_val == ps1_val == py_val, (
            f"{name} disagrees: install.sh={sh_val} install.ps1={ps1_val} config.py={py_val}"
        )
    ok("disk-space budget constants agree across install.sh, install.ps1 and config.py")


def main() -> int:
    if os.name != "nt":
        print("SKIP  native Windows installer test")
        return 0

    check_script_encoding(INSTALLER)
    check_script_encoding(UNINSTALLER)
    test_heartbeat_stall_detection()
    test_heartbeat_zero_tick_edge_case()
    test_disk_space_check()
    check_disk_space_constants_agree()

    with tempfile.TemporaryDirectory(prefix="mnemo install ") as raw:
        mismatched_env = {
            **os.environ,
            "HOME": str(Path(raw) / "different home"),
            "PYTHONUTF8": "1",
        }
        mismatch = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(INSTALLER),
                "-Check",
            ],
            env=mismatched_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert mismatch.returncode != 0
        assert "requires both to match" in mismatch.stderr
        ok("mismatched HOME is refused")

        engine = Path(raw) / "рушій з пробілами"
        install = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "-InstallHome",
            str(engine),
            "-Python",
            sys.executable,
        ]

        first = run(install)
        launcher = engine / "bin" / "mnemo.exe"
        # src/ and .venv live under the versioned tree (versions/<tag>/);
        # `current` is the stable junction mnemo_bootstrap.py resolves at run
        # time (see its module docstring / config.CURRENT_LINK).
        venv_python = engine / "current" / ".venv" / "Scripts" / "python.exe"
        assert launcher.is_file(), launcher
        assert venv_python.is_file(), venv_python
        # Build-EngineVersion runs unconditionally, even for an isolated
        # -InstallHome (the early return for "isolated" comes after it) --
        # so these new heartbeat call sites (2026-08-23: venv creation, pip
        # upgrade, launcher install all used to be silent) must have gone
        # through Invoke-CheckedWithHeartbeat with their own Label, not just
        # exist as functions nothing calls.
        for label in (
            "creating the virtual environment",
            "upgrading pip",
            "installing the mnemo launcher",
        ):
            assert f"install.ps1: {label}" in first.stdout, (label, first.stdout)
        ok("venv creation, pip upgrade and launcher install all went through the heartbeat wrapper")
        # A default install now warms the model, starts the service and runs
        # doctor — one command takes a clean machine all the way. An isolated
        # -InstallHome must do NONE of that: it is a manual or test copy, and
        # this suite is the reason. A 2.2 GB download, a second process
        # claiming port 8918, or a `doctor` reporting the *real* engine would
        # each be a test reaching out and touching the machine.
        assert "skipped the model, the service and the check" in first.stdout
        assert "verifying --" not in first.stdout
        # The banner is a claim; this is the behaviour. "The model is
        # downloaded only by an explicit warmup" is a binding invariant, so
        # assert the cache is genuinely bare rather than trusting the text.
        cache_dir = engine / "model-cache"
        stray = [p for p in cache_dir.rglob("*") if p.is_file()] if cache_dir.exists() else []
        assert not stray, f"install downloaded model files: {stray[:5]}"
        # Nothing was spawned either: an isolated home writes no service state.
        assert not (engine / "state" / "service.pid").exists()
        ok("isolated install downloads no model and starts nothing")

        # The prompt must never fire without a terminal — a scheduled task or
        # a piped run would otherwise hang forever, or read one byte of the
        # caller's data as the answer.
        assert "download it now?" not in first.stdout
        ok("no interactive prompt in a non-interactive run")

        checked = run(install + ["-Check"])
        assert "python deps   present" in checked.stdout
        assert "launcher      present" in checked.stdout
        ok("read-only installer check")

        shadow = Path(raw) / "target project" / "src"
        shadow.mkdir(parents=True)
        (shadow / "__init__.py").write_text("", encoding="utf-8")
        (shadow / "cli.py").write_text(
            'raise RuntimeError("project src shadowed mnemo")\n',
            encoding="utf-8",
        )
        help_result = run([str(launcher), "--help"], cwd=shadow.parent)
        assert "Project memory" in help_result.stdout
        ok("launcher ignores project-local src package")

        extensionless = launcher.with_suffix("")
        direct_result = run([str(extensionless), "--help"])
        assert "Project memory" in direct_result.stdout
        ok("direct process launch resolves mnemo.exe")

        shell_command = f"& '{extensionless}' --help"
        shell_result = run([
            "powershell.exe",
            "-NoProfile",
            "-Command",
            shell_command,
        ])
        assert "Project memory" in shell_result.stdout
        ok("PowerShell hook form resolves mnemo.exe")

        state_sentinel = engine / "state" / "keep.txt"
        cache_sentinel = engine / "model-cache" / "keep.txt"
        state_sentinel.write_text("state", encoding="utf-8")
        cache_sentinel.write_text("cache", encoding="utf-8")

        # A near-complete snapshot must NOT read as warmed. Without the
        # contrasting warmed case below this proves nothing: -Check prints
        # "empty / incomplete" for any engine that never ran warmup — it
        # says so even for an engine home that does not exist.
        spec = run([
            str(venv_python),
            "-c",
            "import sys, json; sys.path.insert(0, sys.argv[1]);"
            " from src.embedder import _model_cache_spec;"
            " repo, files = _model_cache_spec();"
            " print(json.dumps([repo, sorted(files)]))",
            # src/ lives under the versioned tree, resolved through `current`
            # -- not directly under the engine home any more.
            str(engine / "current"),
        ])
        repository, required = json.loads(spec.stdout.strip())
        snapshot = (
            engine
            / "model-cache"
            / f"models--{repository.replace('/', '--')}"
            / "snapshots"
            / "fake-revision"
        )
        snapshot.mkdir(parents=True)
        model_file = next(n for n in required if n.endswith(".onnx"))
        for name in required:
            # Every required file present, but the ONNX graph is truncated.
            (snapshot / name).write_bytes(b"" if name == model_file else b"x")
        incomplete = run(install + ["-Check"])
        assert "model cache   empty / incomplete" in incomplete.stdout, incomplete.stdout
        ok("partial model cache is not reported as warmed")

        (snapshot / model_file).write_bytes(b"x")
        warmed = run(install + ["-Check"])
        assert "model cache   present (warmed)" in warmed.stdout, warmed.stdout
        ok("complete model cache is reported as warmed")

        run(install)
        assert state_sentinel.read_text(encoding="utf-8") == "state"
        assert cache_sentinel.read_text(encoding="utf-8") == "cache"
        ok("reinstall preserves state and model cache")

        # -DepsOnly must refresh the venv without re-mirroring src/, so it is
        # safe to run while the repository's engine code is mid-refactor.
        # src/ lives under `current` (-> versions/local), not the engine root.
        in_flight = engine / "current" / "src" / "in flight.py"
        in_flight.write_text("# uncommitted engine work\n", encoding="utf-8")
        deps_only = run(install + ["-DepsOnly"])
        assert "deps-only" in deps_only.stdout
        assert in_flight.is_file(), "-DepsOnly re-mirrored src/"
        assert state_sentinel.read_text(encoding="utf-8") == "state"
        assert cache_sentinel.read_text(encoding="utf-8") == "cache"
        run([str(launcher), "--help"])
        ok("-DepsOnly refreshes packages only")

        # A v2 engine, recognised by what v2 never had: a banks registry.
        # Every v2 index is orphaned the instant v3 runs -- v2 keyed the file
        # by PROJECT root, v3 by BANK root -- and no v2 database carries a
        # `meta` table, so the path cannot be recovered from inside either.
        # Nothing will ever open them again, so the installer retires them.
        assert not (engine / "state" / "banks.json").exists()
        legacy = [engine / "state" / f"{'ab12cd34ef567890'[:8]}{n:08x}.db"
                  for n in range(3)]
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

        # And it must be a one-time event: with the indexes gone there is
        # nothing to detect, so an ordinary reinstall says nothing about v2.
        quiet = run(install)
        assert "found a v2 engine" not in quiet.stdout, quiet.stdout
        ok("a clean engine reports no v2 leftovers")

        # An isolated -InstallHome must not reach into user scope: no logon
        # task, no profile edit, no environment variable.
        #
        # Asserting "no task exists" was wrong: once the machine has a real
        # engine installed, the logon task legitimately exists and the check
        # failed for the right reason at the wrong target. The invariant is
        # narrower -- whatever task exists must not point at THIS temporary
        # engine home.
        assert "isolated home" in first.stdout, first.stdout
        task = subprocess.run(
            ["schtasks", "/Query", "/TN", "mnemo service", "/XML"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
        if task.returncode == 0:
            assert str(engine) not in task.stdout, (
                "an isolated install redirected the real logon task at itself"
            )
            ok("isolated install left the real logon task pointing elsewhere")
        else:
            ok("isolated install registered no logon task")

        assert (engine / "bin" / "mnemow.exe").is_file()
        ok("both launchers installed (console + windowless)")

        # ---- uninstall, against the same isolated engine -----------------
        # Last, because it destroys what every check above needs. The user
        # scope is captured first: an isolated uninstall must leave the real
        # machine's registrations exactly as it found them, and "exactly" is
        # only checkable against a before-value.
        def user_scope() -> tuple[str, str]:
            probe = run([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command",
                "$token = [Environment]::GetEnvironmentVariable("
                "'MNEMO_API_TOKEN', 'User'); "
                "$profileText = ''; "
                "if (Test-Path -LiteralPath $PROFILE.CurrentUserAllHosts) { "
                "$profileText = Get-Content -LiteralPath "
                "$PROFILE.CurrentUserAllHosts -Raw }; "
                "Write-Host ('TOKEN:' + [bool]$token); "
                "Write-Host ('PROFILE:' + "
                "[bool]($profileText -match 'mnemo'))",
            ])
            lines = dict(
                line.split(":", 1)
                for line in probe.stdout.splitlines()
                if ":" in line
            )
            return lines.get("TOKEN", ""), lines.get("PROFILE", "")

        before_scope = user_scope()

        uninstall = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(UNINSTALLER), "-InstallHome", str(engine),
        ]

        dry = run(uninstall + ["-DryRun"])
        assert "dry run: nothing was removed" in dry.stdout, dry.stdout
        assert "isolated home" in dry.stdout, dry.stdout
        assert launcher.is_file(), "a dry run deleted the engine"
        ok("uninstall -DryRun reports and changes nothing")

        # No terminal and no -Yes: a prompt nobody can see would hang or read
        # one byte of the caller's data as consent. For a delete, refusing is
        # the only safe reading -- and the engine must survive it intact.
        unattended = run(uninstall)
        assert "non-interactive run and no -Yes" in unattended.stdout
        assert launcher.is_file(), "an unconfirmed uninstall deleted the engine"
        ok("uninstall refuses to delete without a terminal or -Yes")

        kept = run(uninstall + ["-KeepModel", "-KeepState", "-Yes"])
        # Engine code + venv live under versions/<tag>/, referenced through
        # the `current` junction -- both must go; state/ and model-cache/
        # (checked below) must not.
        assert not (engine / "versions").exists(), kept.stdout
        assert not (engine / "current").exists(), kept.stdout
        assert not venv_python.exists(), kept.stdout
        assert not launcher.exists(), kept.stdout
        assert state_sentinel.read_text(encoding="utf-8") == "state"
        assert cache_sentinel.read_text(encoding="utf-8") == "cache"
        ok("-KeepModel -KeepState removes the engine but not the data")

        final = run(uninstall + ["-Yes"])
        assert not engine.exists(), final.stdout
        assert "mnemo is gone from this machine" in final.stdout
        ok("full uninstall removes the engine home")

        assert user_scope() == before_scope, (
            "an isolated uninstall reached into user scope"
        )
        ok("isolated uninstall left the token and profile untouched")

        task_after = subprocess.run(
            ["schtasks", "/Query", "/TN", "mnemo service"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
        if task.returncode == 0:
            assert task_after.returncode == 0, (
                "an isolated uninstall removed the real machine's logon task"
            )
            ok("isolated uninstall left the real logon task alone")
        else:
            ok("no logon task existed either side of the isolated uninstall")

        absent = run(uninstall + ["-Yes"])
        assert "no engine installed there" in absent.stdout, absent.stdout
        ok("uninstalling twice is not an error")

    print(f"\n{_passed} passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
