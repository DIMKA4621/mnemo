"""install.ps1 must refresh an engine whose service is running (§15.6 item 4).

The v3 failure mode this guards: the always-on backend holds
``.venv\\Scripts\\python.exe`` open, so a refresh that does not stop it first
dies on a locked file. The installer's answer is stop -> refresh -> start,
restoring the service only if it was up to begin with.

Slow by nature (two full installs against an isolated engine home), and kept
out of the main installer smoke test for that reason. It never touches the
real engine.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install.ps1"

_passed = _failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def run(args: list[str], **kw) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1200, **kw,
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def healthy(port: int, timeout: float = 120.0) -> bool:
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    return False


def main() -> int:
    if os.name != "nt":
        print("SKIP  Windows installer refresh-under-load test")
        return 0
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("SKIP  refresh-under-load (httpx missing)")
        return 0

    with tempfile.TemporaryDirectory(prefix="mnemo refresh ") as raw:
        engine = Path(raw) / "engine home"
        port = free_port()
        install = [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(INSTALLER), "-InstallHome", str(engine),
            "-Python", sys.executable,
        ]
        launcher = engine / "bin" / "mnemo.exe"
        env = {
            **os.environ,
            "MNEMO_STATE_DIR": str(engine / "state"),
            "MNEMO_API_PORT": str(port),
            "PYTHONUTF8": "1",
        }

        first = run(install)
        check("isolated engine installs", first.returncode == 0,
              detail=first.stderr[-300:])
        if first.returncode != 0:
            return 1

        started = run([str(launcher), "service", "start"], env=env)
        check("the service starts from that engine", started.returncode == 0,
              detail=started.stdout + started.stderr)
        check("the backend is serving before the refresh", healthy(port))

        info_path = engine / "state" / "service.json"
        before = json.loads(info_path.read_text(encoding="utf-8"))["pid"]

        try:
            refresh = run(install, env=env)
            check("the installer succeeds while the service holds the venv",
                  refresh.returncode == 0, detail=refresh.stderr[-400:])
            check("it says it stopped the service for the refresh",
                  "stopped for refresh" in refresh.stdout,
                  detail=refresh.stdout[-300:])
            check("it brought the service back up",
                  "started" in refresh.stdout.lower(), detail=refresh.stdout[-300:])
            check("the backend is serving again afterwards", healthy(port))

            after = json.loads(info_path.read_text(encoding="utf-8"))["pid"]
            check("it is a NEW process, i.e. genuinely restarted",
                  after != before, detail=f"{before} -> {after}")
        finally:
            run([str(launcher), "service", "stop"], env=env)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
