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


def check_installer_encoding() -> None:
    """install.ps1 must stay pure ASCII, and must parse.

    Windows PowerShell 5.1 decodes a BOM-less .ps1 as the system ANSI
    codepage, so a single em dash in a double-quoted string breaks the
    quoting and the whole installer fails to parse. That happened; this is
    the guard.
    """
    text = INSTALLER.read_text(encoding="utf-8")
    offenders = [
        (number, line.strip())
        for number, line in enumerate(text.splitlines(), start=1)
        if any(ord(char) > 127 for char in line)
    ]
    assert not offenders, f"non-ASCII in install.ps1: {offenders[:3]}"
    ok("install.ps1 is pure ASCII (PowerShell 5.1 reads it as ANSI)")

    parse = run([
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
        "$errors = $null; "
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{INSTALLER}',"
        " [ref]$null, [ref]$errors); "
        "if ($errors -and $errors.Count -gt 0) { "
        "$errors | ForEach-Object { Write-Host $_.Message }; exit 1 } "
        "else { Write-Host 'parses cleanly' }",
    ])
    assert "parses cleanly" in parse.stdout, parse.stdout
    ok("install.ps1 parses with zero errors")


def main() -> int:
    if os.name != "nt":
        print("SKIP  native Windows installer test")
        return 0

    check_installer_encoding()

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
        assert "model is NOT downloaded" in first.stdout
        # The banner is a claim; this is the behaviour. "The model is
        # downloaded only by an explicit warmup" is a binding invariant, so
        # assert the cache is genuinely bare rather than trusting the text.
        cache_dir = engine / "model-cache"
        stray = [p for p in cache_dir.rglob("*") if p.is_file()] if cache_dir.exists() else []
        assert not stray, f"install downloaded model files: {stray[:5]}"
        ok("fresh Windows install downloads no model")

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

    print(f"\n{_passed} passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
