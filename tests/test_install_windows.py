"""Native Windows installer smoke test (no model download)."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install.ps1"


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


def main() -> int:
    if os.name != "nt":
        print("SKIP  native Windows installer test")
        return 0

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
        print("PASS  mismatched HOME is refused")

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
        print("PASS  fresh Windows install")

        checked = run(install + ["-Check"])
        assert "python deps   present" in checked.stdout
        assert "launcher      present" in checked.stdout
        print("PASS  read-only installer check")

        shadow = Path(raw) / "target project" / "src"
        shadow.mkdir(parents=True)
        (shadow / "__init__.py").write_text("", encoding="utf-8")
        (shadow / "cli.py").write_text(
            'raise RuntimeError("project src shadowed mnemo")\n',
            encoding="utf-8",
        )
        help_result = run([str(launcher), "--help"], cwd=shadow.parent)
        assert "Project memory" in help_result.stdout
        print("PASS  launcher ignores project-local src package")

        extensionless = launcher.with_suffix("")
        direct_result = run([str(extensionless), "--help"])
        assert "Project memory" in direct_result.stdout
        print("PASS  direct process launch resolves mnemo.exe")

        shell_command = f"& '{extensionless}' --help"
        shell_result = run([
            "powershell.exe",
            "-NoProfile",
            "-Command",
            shell_command,
        ])
        assert "Project memory" in shell_result.stdout
        print("PASS  PowerShell hook form resolves mnemo.exe")

        state_sentinel = engine / "state" / "keep.txt"
        cache_sentinel = engine / "model-cache" / "keep.txt"
        state_sentinel.write_text("state", encoding="utf-8")
        cache_sentinel.write_text("cache", encoding="utf-8")
        incomplete = run(install + ["-Check"])
        assert "empty / incomplete" in incomplete.stdout
        print("PASS  partial model cache is not reported as warmed")

        run(install)
        assert state_sentinel.read_text(encoding="utf-8") == "state"
        assert cache_sentinel.read_text(encoding="utf-8") == "cache"
        print("PASS  reinstall preserves state and model cache")

    print("\n8 passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
