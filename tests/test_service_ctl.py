"""Lifecycle + windowless-spawn checks for src/service_ctl.py (block L).

Runs against a trivial long-running script, so it is independent of the
backend. The console-window checks are evidence, not assertion: the child
itself reports whether it owns a console (and whether that console is
visible), and the same probe is run against deliberately *wrong* spawn modes
so a detector that never fires cannot pass for a proof.
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

# Redirect all writable state into a temp dir BEFORE config is imported —
# the real state/ must never be touched by a test.
_STATE = Path(tempfile.mkdtemp(prefix="mnemo service "))
os.environ["MNEMO_STATE_DIR"] = str(_STATE)

from src import service_ctl  # noqa: E402

_passed = _failed = 0

# A child that reports what it can see about its own console, then idles
# until a stop file appears. With --alloc it first calls AllocConsole(),
# which is what turns "no console" into "a window on screen" when the spawn
# flags are wrong.
CHILD = '''\
import ctypes, json, os, sys, time

report, stopfile = sys.argv[1], sys.argv[2]
alloc = "--alloc" in sys.argv

console, visible = 0, 0
if os.name == "nt":
    k32 = ctypes.WinDLL("kernel32")
    u32 = ctypes.WinDLL("user32")
    if alloc:
        k32.AllocConsole()
    console = int(k32.GetConsoleWindow() or 0)
    if console:
        visible = int(u32.IsWindowVisible(console) or 0)

with open(report, "w", encoding="utf-8") as fh:
    json.dump({"pid": os.getpid(), "console": console,
               "visible": visible, "executable": sys.executable}, fh)

if alloc:
    raise SystemExit(0)
while not os.path.exists(stopfile):
    time.sleep(0.1)
'''


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def wait_for(path: Path, timeout: float = 15.0) -> dict | None:
    """Wait for the child's report file and return it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                pass  # caught mid-write
        time.sleep(0.05)
    return None


def console_window_pids() -> set[int]:
    """PIDs owning a top-level console window right now (Windows only)."""
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
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.add(int(pid.value))
        return True

    user32.EnumWindows(visit, 0)
    return found


def test_windowless_spawn(work: Path, script: Path) -> None:
    """The heart of NFR-1, proven three ways."""
    stop_file = work / "stop"

    # 1. The primitive itself, repeated — a flash that happens 1 time in 20
    #    is still a flash.
    consoles, window_sightings = [], []
    for run in range(20):
        report = work / f"report-{run}.json"
        pid = service_ctl.spawn_detached(
            [service_ctl.windowless_python(), str(script), str(report), str(stop_file)]
        )
        # Sweep for a console window belonging to this child while it starts.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if pid in console_window_pids():
                window_sightings.append(pid)
                break
            if (report).is_file():
                break
            time.sleep(0.02)
        data = wait_for(report)
        consoles.append(None if data is None else data["console"])
        stop_file.write_text("stop", encoding="utf-8")
        time.sleep(0.15)
        stop_file.unlink(missing_ok=True)

    check(
        "20 windowless launches: child reports no console",
        all(value == 0 for value in consoles),
        detail=f"console handles: {consoles}",
    )
    check(
        "20 windowless launches: no console window ever owned by the child",
        not window_sightings,
        detail=f"sightings: {window_sightings}",
    )

    if os.name != "nt":
        print("SKIP  Windows-only spawn-mode comparison")
        return

    check(
        "background interpreter is GUI-subsystem pythonw.exe",
        Path(service_ctl.windowless_python()).name.lower().startswith("pythonw"),
        detail=service_ctl.windowless_python(),
    )

    # 2. Inherited-console control, meaningful only from a real terminal. A
    #    detached test runner has no console of its own to hand down, so the
    #    check states which case it is instead of silently proving nothing.
    import ctypes

    parent_console = int(ctypes.WinDLL("kernel32").GetConsoleWindow() or 0)
    if parent_console:
        report = work / "control.json"
        control = subprocess.Popen(
            [sys.executable, str(script), str(report), str(stop_file)]
        )
        data = wait_for(report)
        check(
            "control: a plain spawn inherits the parent console",
            data is not None and data["console"] != 0,
            detail=str(data),
        )
        stop_file.write_text("stop", encoding="utf-8")
        control.wait(timeout=10)
        stop_file.unlink(missing_ok=True)
    else:
        print("SKIP  inherited-console control (test runner has no console)")

    # 3. Why DETACHED_PROCESS alone is not enough (v3 rules). Let the child
    #    call AllocConsole — the thing that turns "no console" into a window
    #    on screen. Under DETACHED_PROCESS it gets a REAL VISIBLE window;
    #    under CREATE_NO_WINDOW the console cannot have a window at all, so
    #    GetConsoleWindow stays NULL. That contrast is the whole rule.
    modes = {
        "DETACHED_PROCESS": 0x00000008,
        "CREATE_NO_WINDOW": service_ctl._CREATE_NO_WINDOW,
    }
    visibility = {}
    for label, flag in modes.items():
        report = work / f"alloc-{label}.json"
        subprocess.Popen(
            [sys.executable, str(script), str(report), str(stop_file), "--alloc"],
            creationflags=flag,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        visibility[label] = wait_for(report)

    detached = visibility["DETACHED_PROCESS"]
    no_window = visibility["CREATE_NO_WINDOW"]
    check(
        "DETACHED_PROCESS lets an allocated console become a VISIBLE window",
        detached is not None and detached["console"] != 0 and detached["visible"] == 1,
        detail=str(detached),
    )
    check(
        "CREATE_NO_WINDOW: an allocated console has no window at all",
        no_window is not None and no_window["console"] == 0,
        detail=str(no_window),
    )


def test_lifecycle(work: Path, script: Path) -> None:
    stop_file = work / "lifecycle-stop"
    report = work / "lifecycle.json"
    target = [
        service_ctl.windowless_python(),
        str(script),
        str(report),
        str(stop_file),
    ]

    check("status before start is 'stopped'", service_ctl.status() == service_ctl.EXIT_DOWN)
    check("stop with nothing running returns 'down'", service_ctl.stop() == service_ctl.EXIT_DOWN)

    check("start returns OK", service_ctl.start(target=target) == service_ctl.EXIT_OK)
    first = service_ctl.probe()
    check("status after start is 'running'", service_ctl.status() == service_ctl.EXIT_OK)
    check("state file records pid + fingerprint",
          bool(first.info and first.info.get("fingerprint")), detail=str(first.info))

    check("start is idempotent (no second process)",
          service_ctl.start(target=target) == service_ctl.EXIT_OK
          and service_ctl.probe().pid == first.pid)

    check("stop returns OK", service_ctl.stop() == service_ctl.EXIT_OK)
    check("stop really reaped the child", not service_ctl._pid_alive(int(first.pid or 0)))
    check("stop removed the state file", service_ctl.read_service_info() is None)
    check("status after stop is 'stopped'", service_ctl.status() == service_ctl.EXIT_DOWN)

    # Hard kill: status must not keep claiming the service is up.
    service_ctl.start(target=target)
    killed = service_ctl.probe()
    pid = int(killed.pid or 0)
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, check=False)
    else:
        os.kill(pid, 9)
    time.sleep(0.6)
    check("status is truthful after a hard kill", service_ctl.status() == service_ctl.EXIT_DOWN)
    check("hard kill leaves the file marked stale", service_ctl.probe().stale)

    # A stale file must not be believed even when its PID is alive again.
    service_ctl._write_service_info(
        {"pid": os.getpid(), "fingerprint": "0:0", "started_at": "?", "python": "x"}
    )
    check("live PID + wrong fingerprint is NOT running (PID reuse)",
          not service_ctl.probe().running)
    check("PID-reuse case reports 'stopped'", service_ctl.status() == service_ctl.EXIT_DOWN)

    service_ctl._write_service_info(
        {"pid": 999_999_999, "fingerprint": "0:0", "started_at": "?", "python": "x"}
    )
    check("dead PID in state file is NOT running", not service_ctl.probe().running)

    check("start clears the stale file and starts",
          service_ctl.start(target=target) == service_ctl.EXIT_OK)
    running = service_ctl.probe()
    check("restart replaces the process",
          service_ctl.restart(target=target) == service_ctl.EXIT_OK
          and service_ctl.probe().pid != running.pid)
    check("restarted process is alive", service_ctl.probe().running)
    service_ctl.stop()
    check("final stop leaves nothing running", service_ctl.status() == service_ctl.EXIT_DOWN)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mnemo svc ") as raw:
        work = Path(raw)
        script = work / "long running child.py"
        script.write_text(CHILD, encoding="utf-8")
        test_windowless_spawn(work, script)
        test_lifecycle(work, script)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
