"""Bootstrap-installer smoke test: get.ps1 / get.sh, no git clone.

get.ps1/get.sh have exactly one job: fetch a source snapshot from GitHub,
extract it, and hand off to the real install.ps1/install.sh -- unmodified --
from inside that extracted copy. install.ps1/install.sh are already proven
by test_install_windows.py / test_install_posix.py; this file proves only
the bootstrap step itself (download, extract, forward, clean up), the same
way test_engine_update.py's test_stage_release_real_pipeline proves the
self-update download step against a local HTTP server standing in for
GitHub -- there is no real release yet to fetch from either.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
GET_PS1 = REPO / "get.ps1"
GET_SH = REPO / "get.sh"
INSTALLER_PS1 = REPO / "install.ps1"

_passed = 0


def ok(name: str) -> None:
    global _passed
    _passed += 1
    print(f"PASS  {name}")


def run(command: list[str], *, env: dict | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def check_script_encoding(script: Path) -> None:
    """Same guard as test_install_windows.py: a BOM-less .ps1 is decoded as
    the system ANSI codepage on Windows PowerShell 5.1, so any non-ASCII
    character breaks quoting and the whole script fails to parse silently
    on some machines. This must never regress for a script people are about
    to pipe straight into `iex` with no chance to inspect it first.
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


def _make_local_source_zip(dest: Path) -> Path:
    """Package THIS repo's own working tree the way GitHub's archive wraps
    a branch -- one top-level "<repo>-<ref>" directory -- so get.ps1's
    download+extract+forward path can be exercised against real, current
    code without a real GitHub release or a network call. Mirrors
    test_engine_update.py's _make_local_release_tarball, .zip instead of
    .tar.gz because that is what get.ps1 actually downloads on Windows.
    """
    wrap = "mnemo-master"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("install.ps1", "requirements.txt", "pyproject.toml", "mnemo_bootstrap.py"):
            zf.write(REPO / name, arcname=f"{wrap}/{name}")
        for path in (REPO / "src").rglob("*"):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            zf.write(path, arcname=f"{wrap}/src/{path.relative_to(REPO / 'src').as_posix()}")
    return dest


def _leftover_temp_dirs() -> set[str]:
    base = Path(tempfile.gettempdir())
    return {p.name for p in base.glob("mnemo-src-*") if p.is_dir()}


def test_get_ps1_windows(work: Path) -> None:
    if os.name != "nt":
        print("SKIP  get.ps1 bootstrap test (Windows-only)")
        return

    import functools
    import http.server
    import threading as th

    check_script_encoding(GET_PS1)

    archive = _make_local_source_zip(work / "local-source.zip")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(work))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    server_thread = th.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    engine = work / "isolated engine"
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "MNEMO_GET_ARCHIVE_URL": f"http://127.0.0.1:{port}/{archive.name}",
    }

    try:
        before = _leftover_temp_dirs()

        result = run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(GET_PS1),
                "-InstallHome", str(engine),
                "-Python", sys.executable,
            ],
            env=env,
            timeout=600,
        )
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
        assert result.returncode == 0, result.stdout
        ok("get.ps1 downloads, extracts and installs via a local archive")

        launcher = engine / "bin" / "mnemo.exe"
        venv_python = engine / "current" / ".venv" / "Scripts" / "python.exe"
        assert launcher.is_file(), launcher
        assert venv_python.is_file(), venv_python
        ok("bootstrap install ends in the same state a direct install.ps1 run would")

        assert _leftover_temp_dirs() == before, "get.ps1 left its temp source copy behind"
        ok("temp source copy is removed after a successful run")

        failing_env = {**env, "MNEMO_GET_ARCHIVE_URL": f"http://127.0.0.1:{port}/does-not-exist.zip"}
        failed = run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(GET_PS1)],
            env=failing_env,
            timeout=60,
        )
        assert failed.returncode != 0
        ok("get.ps1 fails loudly when the archive can't be fetched")
        assert _leftover_temp_dirs() == before, "a failed run left its temp source copy behind"
        ok("temp source copy is removed after a failed run too")
    finally:
        httpd.shutdown()
        server_thread.join(timeout=5)


def test_get_sh_syntax() -> None:
    """The real download+extract+forward pipeline is only exercised on
    POSIX (see test_install_posix.py's own os.name=="nt" skip -- this repo
    treats Git Bash on Windows as a dev convenience, not the POSIX target).
    Here, only a cheap syntax gate that runs wherever bash is reachable.
    """
    import shutil

    bash = shutil.which("bash")
    if not bash:
        print("SKIP  get.sh syntax check (no bash on PATH)")
        return
    result = run([bash, "-n", str(GET_SH)])
    assert result.returncode == 0, result.stderr
    ok("get.sh passes `bash -n` syntax check")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mnemo get-bootstrap ") as raw:
        work = Path(raw)
        test_get_ps1_windows(work)
        test_get_sh_syntax()

    print(f"\n{_passed} passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
