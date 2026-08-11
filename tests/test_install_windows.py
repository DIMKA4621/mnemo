"""Native Windows installer smoke test (no model download)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

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


def main() -> int:
    if os.name != "nt":
        print("SKIP  native Windows installer test")
        return 0

    check_script_encoding(INSTALLER)
    check_script_encoding(UNINSTALLER)

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
        venv_python = engine / ".venv" / "Scripts" / "python.exe"
        assert launcher.is_file(), launcher
        assert venv_python.is_file(), venv_python
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
            str(engine),
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
        in_flight = engine / "src" / "in flight.py"
        in_flight.write_text("# uncommitted engine work\n", encoding="utf-8")
        deps_only = run(install + ["-DepsOnly"])
        assert "deps-only" in deps_only.stdout
        assert in_flight.is_file(), "-DepsOnly re-mirrored src/"
        assert state_sentinel.read_text(encoding="utf-8") == "state"
        assert cache_sentinel.read_text(encoding="utf-8") == "cache"
        run([str(launcher), "--help"])
        ok("-DepsOnly refreshes packages only")

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
        assert not (engine / ".venv").exists(), kept.stdout
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
