"""Restart survival: autostart path + reconcile-on-start (phase 5 acceptance).

Covers the two halves of «Перезавантаження машини -> сервіс піднявся сам,
нічого не втрачено» that can be established without rebooting the machine:

1. **The autostart path really starts the service.** A Task Scheduler task is
   registered from the production XML (hidden, logon trigger) under a
   throwaway name, then triggered. What has to come up is the actual backend
   answering /health -- not a probe script that merely proves a process ran.
2. **Nothing is lost while the service is down.** Files are added, edited and
   deleted with the service stopped; on the next start ``reconcile-on-start``
   must pick up all three.

What is NOT covered here, and cannot be: that Windows fires the logon trigger
after a genuine reboot. That needs a real reboot on the user's machine.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

_STATE = Path(tempfile.mkdtemp(prefix="mnemo recovery "))
os.environ["MNEMO_STATE_DIR"] = str(_STATE)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from src import autostart, service_ctl  # noqa: E402
from src.config import EMBED_PORT  # noqa: E402

_passed = _failed = 0

# This suite indexes a real bank, so it starts the embedding resident. With
# MNEMO_EMBED_IDLE_TIMEOUT defaulting to 0 it never exits on its own, so the
# suite must reap it -- but only if it was not already running before we
# began, in which case it is the user's and not ours to touch.
_PREEXISTING_RESIDENT = service_ctl._listening_pid(EMBED_PORT) is not None

TEST_TASK = "mnemo recovery rehearsal (delete me)"

# Runs the real CLI with the environment a scheduled task would not inherit.
WRAPPER = '''\
import os, sys

os.environ["MNEMO_STATE_DIR"] = {state!r}
os.environ["MNEMO_API_PORT"] = {port!r}
os.chdir({repo!r})
sys.path.insert(0, {repo!r})

from src.cli import main

raise SystemExit(main())
'''


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def token() -> str:
    return (_STATE / "api.token").read_text(encoding="utf-8").strip()


def wait_healthy(port: int, timeout: float = 90.0) -> bool:
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


def wait_gone(port: int, timeout: float = 30.0) -> bool:
    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
        except httpx.HTTPError:
            return True
        time.sleep(0.25)
    return False


def indexed_files(bank_root: Path) -> set[str]:
    """Read the bank's index directly -- the ground truth, not an API view."""
    from src import config, store

    paths = config.resolve(bank_root)
    if not paths.db.exists():
        return set()
    conn = store.connect(paths.db)
    try:
        rows = conn.execute("select path from files").fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def wait_for_files(bank_root: Path, expected: set[str], timeout: float = 300.0) -> set[str]:
    """Poll with a deadline -- embedding time varies far too much to sleep."""
    deadline = time.monotonic() + timeout
    seen: set[str] = set()
    while time.monotonic() < deadline:
        seen = indexed_files(bank_root)
        if seen == expected:
            return seen
        time.sleep(1.0)
    return seen


def start_service(port: int) -> int:
    target = [
        service_ctl.windowless_python(),
        "-m", "src.cli", "serve", "--port", str(port),
    ]
    return service_ctl.start(target=target)


def test_reconcile_on_start(work: Path) -> None:
    """Changes made while the service was down must not be lost."""
    try:
        import httpx
    except ImportError:
        print("SKIP  reconcile-on-start (httpx missing)")
        return

    bank = work / "bank"
    bank.mkdir()
    (bank / "alpha.md").write_text("# Alpha\nfirst note\n", encoding="utf-8")
    (bank / "beta.md").write_text("# Beta\nsecond note\n", encoding="utf-8")

    port = free_port()
    os.environ["MNEMO_API_PORT"] = str(port)
    check("service starts for the first time", start_service(port) == service_ctl.EXIT_OK)
    check("backend is healthy", wait_healthy(port))

    reply = httpx.post(
        f"http://127.0.0.1:{port}/api/banks",
        json={"root": str(bank), "name": "recovery"},
        headers={"Authorization": f"Bearer {token()}"},
        timeout=30.0,
    )
    check("bank registered over the API", reply.status_code in (200, 201),
          detail=f"{reply.status_code} {reply.text[:200]}")

    first = wait_for_files(bank, {"alpha.md", "beta.md"})
    check("initial index contains both files", first == {"alpha.md", "beta.md"},
          detail=str(first))
    if first != {"alpha.md", "beta.md"}:
        service_ctl.stop()
        return

    check("service stops", service_ctl.stop() == service_ctl.EXIT_OK)
    check("the port is free again", wait_gone(port))

    # --- the whole point: mutate the bank while nothing is watching ---
    (bank / "alpha.md").unlink()                                    # delete
    (bank / "beta.md").write_text("# Beta\nEDITED while down\n", encoding="utf-8")
    (bank / "gamma.md").write_text("# Gamma\nadded while down\n", encoding="utf-8")

    check("service starts again", start_service(port) == service_ctl.EXIT_OK)
    check("backend is healthy after restart", wait_healthy(port))

    after = wait_for_files(bank, {"beta.md", "gamma.md"})
    check("reconcile-on-start indexed the file ADDED while down",
          "gamma.md" in after, detail=str(after))
    check("reconcile-on-start PRUNED the file deleted while down",
          "alpha.md" not in after, detail=str(after))

    # The edit is only proven by the content actually reaching the index.
    found = httpx.post(
        f"http://127.0.0.1:{port}/api/search",
        json={"bank": "recovery", "query": "EDITED while down", "k": 5},
        headers={"Authorization": f"Bearer {token()}"},
        timeout=60.0,
    )
    hits = found.json().get("hits", []) if found.status_code == 200 else []
    check("reconcile-on-start re-embedded the file EDITED while down",
          any("EDITED" in (hit.get("content") or "") for hit in hits),
          detail=f"{found.status_code} {str(hits)[:300]}")

    service_ctl.stop()
    wait_gone(port)


def test_autostart_path_starts_the_service(work: Path) -> None:
    """Task Scheduler -> hidden -> windowless -> a backend that serves."""
    if os.name != "nt":
        print("SKIP  Task Scheduler rehearsal (not Windows)")
        return
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("SKIP  Task Scheduler rehearsal (httpx missing)")
        return

    port = free_port()
    wrapper = work / "scheduled launcher.py"
    wrapper.write_text(
        WRAPPER.format(state=str(_STATE), port=str(port), repo=str(REPO)),
        encoding="utf-8",
    )

    xml = autostart.task_xml(
        Path(service_ctl.windowless_python()),
        arguments=f'"{wrapper}" service start',
        task_name=TEST_TASK,
    )
    xml_path = work / "recovery-task.xml"
    xml_path.write_text(xml, encoding="utf-16")

    created = subprocess.run(
        ["schtasks", "/Create", "/TN", TEST_TASK, "/XML", str(xml_path), "/F"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    check("the logon task registers", created.returncode == 0,
          detail=f"{created.stdout.strip()} {created.stderr.strip()}")
    if created.returncode != 0:
        return

    try:
        stored = subprocess.run(
            ["schtasks", "/Query", "/TN", TEST_TASK, "/XML"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        ).stdout
        check("Windows stored it as hidden", "<Hidden>true</Hidden>" in stored)
        check("Windows stored a logon trigger", "<LogonTrigger>" in stored)

        before = _console_windows()
        run = subprocess.run(
            ["schtasks", "/Run", "/TN", TEST_TASK],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        check("the scheduler runs it", run.returncode == 0, detail=run.stdout.strip())

        # The real acceptance signal: a backend answering on loopback.
        check("the autostart path brought up a SERVING backend",
              wait_healthy(port, timeout=120.0))

        appeared = _console_windows() - before
        check("no console window appeared during autostart",
              not appeared, detail=str(appeared))

        state = service_ctl.probe()
        check("the service it started is manageable from here",
              state.running and state.managed, detail=str(state))
        check("stop works on a service started by the scheduler",
              service_ctl.stop() == service_ctl.EXIT_OK)
    finally:
        subprocess.run(["schtasks", "/Delete", "/TN", TEST_TASK, "/F"],
                       capture_output=True, timeout=60)
        service_ctl.stop()


def _console_windows() -> set[int]:
    if os.name != "nt":
        return set()
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    found: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, name, 256)
        if name.value == "ConsoleWindowClass":
            found.add(int(hwnd))
        return True

    user32.EnumWindows(visit, 0)
    return found


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mnemo recovery ") as raw:
        work = Path(raw)
        try:
            test_reconcile_on_start(work)
            test_autostart_path_starts_the_service(work)
        finally:
            service_ctl.stop()
            if not _PREEXISTING_RESIDENT:
                if service_ctl.stop_resident() == service_ctl.RESIDENT_STOPPED:
                    print("teardown: reaped the embedding resident this suite started")

    print(f"\n{_passed} passed, {_failed} failed")
    print(
        "\nNOT PROVEN HERE: that Windows fires the logon trigger after a real "
        "reboot.\nThe task is registered, stored hidden, and starts a serving "
        "backend on demand;\nonly an actual reboot can confirm the trigger itself."
    )
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
