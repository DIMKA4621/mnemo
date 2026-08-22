"""Bootstrap-installer smoke test: get.ps1 / get.sh, no git clone.

get.ps1/get.sh have exactly one job: fetch a source snapshot from GitHub,
extract it, and hand off to the real install.ps1/install.sh -- unmodified --
from inside that extracted copy. install.ps1/install.sh are already proven
by test_install_windows.py / test_install_posix.py; this file proves only
the bootstrap step itself (resolve a ref, download, extract, forward, clean
up), the same way test_engine_update.py's test_stage_release_real_pipeline
proves the self-update download step against a local HTTP server standing
in for GitHub -- a local server here too, so these tests stay deterministic
and network-free even though a real release now exists.
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


def _make_stub_install_zip(dest: Path) -> Path:
    """A minimal archive whose "install.ps1" only records the args it
    received, for testing get.ps1's own -Model default-forwarding logic
    without running the real (network- and time-heavy) install pipeline.

    Writes to $env:MNEMO_TEST_MARKER rather than anywhere under
    $PSScriptRoot: get.ps1 deletes its own temp copy (which is where the
    stub lives) in its `finally` block before this test process gets to
    read the marker back, so the marker must live outside that temp dir.
    """
    wrap = "mnemo-master"
    stub = (
        "$args | Out-File -FilePath $env:MNEMO_TEST_MARKER -Encoding utf8\n"
    )
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{wrap}/install.ps1", stub)
    return dest


def test_get_ps1_default_model_flag(work: Path) -> None:
    """get.ps1 must add -Model by default (a one-liner should finish the
    job in one command), but never when -Model/-NoModel was already passed
    -- this is the behaviour that differs from install.ps1's own default,
    so it gets its own fast, network-free check against a stub install.ps1.
    """
    if os.name != "nt":
        print("SKIP  get.ps1 default -Model test (Windows-only)")
        return

    import functools
    import http.server
    import threading as th

    check_script_encoding(GET_PS1)

    archive = _make_stub_install_zip(work / "stub-install.zip")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(work))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    server_thread = th.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    marker = work / "received-args.txt"
    env = {
        **os.environ,
        "MNEMO_GET_ARCHIVE_URL": f"http://127.0.0.1:{port}/{archive.name}",
        "MNEMO_TEST_MARKER": str(marker),
    }

    def received_args() -> list[str]:
        assert marker.is_file(), "stub install.ps1 never ran"
        try:
            # PowerShell's `Out-File -Encoding utf8` writes a BOM; "utf-8"
            # keeps it as a literal ﻿ on the first line, "utf-8-sig"
            # strips it.
            text = marker.read_text(encoding="utf-8-sig")
            return [line.strip() for line in text.splitlines() if line.strip()]
        finally:
            marker.unlink()

    try:
        run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(GET_PS1)], env=env, timeout=60)
        assert "-Model" in received_args(), "get.ps1 did not default to -Model"
        ok("get.ps1 adds -Model by default when neither -Model nor -NoModel was passed")

        run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(GET_PS1), "-NoModel"], env=env, timeout=60)
        args_with_nomodel = received_args()
        assert "-NoModel" in args_with_nomodel
        assert "-Model" not in args_with_nomodel
        ok("get.ps1 respects an explicit -NoModel and does not also add -Model")

        run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(GET_PS1), "-Model"], env=env, timeout=60)
        args_with_model = received_args()
        assert args_with_model.count("-Model") == 1, "get.ps1 double-added -Model"
        ok("get.ps1 does not duplicate an already-explicit -Model")
    finally:
        httpd.shutdown()
        server_thread.join(timeout=5)


def _json_handler(payload: bytes):
    import functools
    import http.server

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's own naming
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):  # silence per-request stderr noise
            pass

    return functools.partial(_Handler)


def test_get_ps1_resolves_release_tag(work: Path) -> None:
    """get.ps1 must default to the latest GitHub release, not `master`
    (2026-08-22 request). MNEMO_GET_RELEASE_API_URL is a test-only override
    on the release lookup itself, so the tag_name it returns can be a local
    fixture -- but the resulting codeload URL is still real, since get.ps1
    takes no override for that. A made-up tag 404s there almost instantly
    (no multi-MB download, no install ever starts), which is enough to
    prove the resolved tag actually reached the download step unmangled --
    the same "real network, skip if offline" tolerance test_engine_update.py
    already relies on elsewhere in this repo, kept to the smallest possible
    real request.
    """
    if os.name != "nt":
        print("SKIP  get.ps1 release-resolution test (Windows-only)")
        return

    import http.server
    import socket
    import threading as th

    try:
        socket.create_connection(("codeload.github.com", 443), timeout=5).close()
    except OSError:
        print("SKIP  get.ps1 release-resolution test (no network)")
        return

    fake_tag = "v9.9.9-mnemo-test-fixture"
    handler = _json_handler(f'{{"tag_name": "{fake_tag}"}}'.encode())
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    server_thread = th.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    env = {
        **os.environ,
        "MNEMO_GET_RELEASE_API_URL": f"http://127.0.0.1:{port}/releases/latest",
    }

    try:
        result = run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(GET_PS1)],
            env=env, timeout=30,
        )
        assert f"downloading mnemo ({fake_tag})" in result.stdout, result.stdout
        ok("get.ps1 resolves the tag_name from releases/latest and labels the download with it")
        assert result.returncode != 0, "a made-up tag should not exist on codeload"
        ok("the resolved tag reaches the real codeload URL unmangled (404, not a malformed request)")
    finally:
        httpd.shutdown()
        server_thread.join(timeout=5)


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
                "-NoModel",
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
        test_get_ps1_default_model_flag(work)
        test_get_ps1_resolves_release_tag(work)
        test_get_ps1_windows(work)
        test_get_sh_syntax()

    print(f"\n{_passed} passed, 0 failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
