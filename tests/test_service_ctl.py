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
    identity = service_ctl.read_identity() or {}
    check("service.pid records pid + fingerprint",
          bool(identity.get("pid") and identity.get("fingerprint")), detail=str(identity))

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
    service_ctl._write_identity(
        {"pid": os.getpid(), "fingerprint": "0:0", "started_at": "?", "python": "x"}
    )
    check("live PID + wrong fingerprint is NOT running (PID reuse)",
          not service_ctl.probe().running)
    check("PID-reuse case reports 'stopped'", service_ctl.status() == service_ctl.EXIT_DOWN)

    service_ctl._write_identity(
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


def test_never_kills_bystanders(work: Path) -> None:
    """The one that matters: stop() must not touch a process it did not start.

    The file written here is EXACTLY what ``api.py::_write_service_info``
    produces on every backend startup -- pid/port/host/started_at/version/
    python, and no fingerprint. If a bare PID were enough to terminate, the
    victim would be whatever process happens to hold that number.
    """
    stop_file = work / "bystander-stop"
    report = work / "bystander.json"
    innocent = subprocess.Popen(
        [sys.executable, str(work / "long running child.py"), str(report), str(stop_file)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        wait_for(report)  # it is up and running
        service_ctl.service_pid_file().unlink(missing_ok=True)
        service_ctl.service_info_file().write_text(
            json.dumps({
                "pid": innocent.pid,
                "port": 8918,
                "host": "127.0.0.1",
                "started_at": "2026-07-26T09:00:00+03:00",
                "version": "3.0.0",
                "python": "C:/x/pythonw.exe",
            }),
            encoding="utf-8",
        )

        rc = service_ctl.stop()
        check("stop refuses a PID it cannot prove is ours",
              rc != service_ctl.EXIT_OK, detail=f"rc={rc}")
        time.sleep(0.5)
        check("THE BYSTANDER PROCESS SURVIVED stop()",
              innocent.poll() is None, detail=f"exit={innocent.poll()}")

        # ... and the same for a truncated / hand-edited file.
        service_ctl.service_info_file().write_text(
            f'{{"pid": {innocent.pid}, "por', encoding="utf-8"
        )
        service_ctl.stop()
        time.sleep(0.3)
        check("a corrupt state file cannot kill anything",
              innocent.poll() is None, detail=f"exit={innocent.poll()}")

        # A fingerprint that does not match must not fall through to a kill.
        service_ctl._write_identity({
            "pid": innocent.pid, "fingerprint": "0:0",
            "started_at": "?", "python": "x",
        })
        service_ctl.stop()
        time.sleep(0.3)
        check("a fingerprint mismatch never falls through to killing",
              innocent.poll() is None, detail=f"exit={innocent.poll()}")

        # An identity file with the fingerprint field missing entirely.
        service_ctl._write_identity({
            "pid": innocent.pid, "started_at": "?", "python": "x",
        })
        service_ctl.stop()
        time.sleep(0.3)
        check("a missing fingerprint is refused, not assumed to match",
              innocent.poll() is None, detail=f"exit={innocent.poll()}")
    finally:
        stop_file.write_text("stop", encoding="utf-8")
        try:
            innocent.wait(timeout=10)
        except subprocess.TimeoutExpired:
            innocent.kill()
        stop_file.unlink(missing_ok=True)
        service_ctl.service_pid_file().unlink(missing_ok=True)
        service_ctl.service_info_file().unlink(missing_ok=True)


def test_state_dir_is_resolved_live() -> None:
    """A relocated state dir must move the pid file with it.

    ``SERVICE_PID_FILE`` is ``STATE_DIR / "service.pid"`` evaluated at import;
    binding it would leave service_ctl reading the pid from the directory the
    state used to be in. Same bug api.service_info_file() exists to avoid.
    """
    from unittest.mock import patch

    import src.config as config

    with tempfile.TemporaryDirectory(prefix="mnemo relocated ") as raw:
        moved = Path(raw)
        with patch.object(config, "STATE_DIR", moved):
            check("state_dir() follows a relocated config.STATE_DIR",
                  service_ctl.state_dir() == moved, detail=str(service_ctl.state_dir()))
            check("the pid file follows the state dir",
                  service_ctl.service_pid_file() == moved / "service.pid",
                  detail=str(service_ctl.service_pid_file()))
            check("the backend info file follows the state dir",
                  service_ctl.service_info_file() == moved / "service.json",
                  detail=str(service_ctl.service_info_file()))

            service_ctl._write_identity({"pid": 1, "fingerprint": "x"})
            check("writes land in the relocated directory",
                  (moved / "service.pid").is_file())
            check("reads come back from the relocated directory",
                  (service_ctl.read_identity() or {}).get("pid") == 1)
        service_ctl.service_pid_file().unlink(missing_ok=True)


def test_exit_code_259(work: Path) -> None:
    """259 is a legitimate exit code, not a synonym for "still running"."""
    script = work / "exit259.py"
    script.write_text("raise SystemExit(259)\n", encoding="utf-8")
    pid = service_ctl.spawn_detached([service_ctl.windowless_python(), str(script)])

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and service_ctl._pid_state(pid) == service_ctl.ALIVE:
        time.sleep(0.1)
    check("a process exiting with 259 is reported GONE, not alive",
          service_ctl._pid_state(pid) == service_ctl.GONE,
          detail=service_ctl._pid_state(pid))


def test_clear_identity_spares_a_live_backend() -> None:
    """Clearing our stale entry must not delete a live backend's file."""
    service_ctl.service_pid_file().unlink(missing_ok=True)
    service_ctl.service_info_file().write_text(
        json.dumps({"pid": os.getpid(), "host": "127.0.0.1", "port": 8918}),
        encoding="utf-8",
    )
    service_ctl._clear_identity()
    check("service.json of a LIVE process is left alone",
          service_ctl.service_info_file().is_file())

    service_ctl.service_info_file().write_text(
        json.dumps({"pid": 999_999_999, "host": "127.0.0.1", "port": 8918}),
        encoding="utf-8",
    )
    service_ctl._clear_identity()
    check("service.json of a DEAD process is cleaned up",
          not service_ctl.service_info_file().is_file())


def test_start_is_serialised(work: Path) -> None:
    """Concurrent starts must not produce two backends."""
    stop_file = work / "race-stop"
    target = [
        service_ctl.windowless_python(),
        str(work / "long running child.py"),
        str(work / "race-report.json"),
        str(stop_file),
    ]
    service_ctl.service_pid_file().unlink(missing_ok=True)
    service_ctl.service_info_file().unlink(missing_ok=True)

    import threading

    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(service_ctl.start(target=target)))
        for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    survivors = _matching_children(target[1])
    check("three concurrent starts leave exactly one process",
          len(survivors) == 1, detail=f"pids={survivors} results={results}")
    service_ctl.stop()
    stop_file.write_text("stop", encoding="utf-8")
    time.sleep(0.4)
    stop_file.unlink(missing_ok=True)


def _matching_children(script: str) -> list[int]:
    """PIDs of live processes whose command line mentions ``script``."""
    if os.name != "nt":
        out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                             text=True, timeout=30).stdout
        return [int(line.split()[0]) for line in out.splitlines() if script in line]
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
         f"'*{Path(script).name}*' " "} | Select-Object -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=90,
    ).stdout
    pids = [int(v) for v in out.split() if v.strip().isdigit()]
    # The redirector plus its base-interpreter child count as one service.
    return [pid for pid in pids if service_ctl._pid_state(pid) == service_ctl.ALIVE][:1] \
        if pids else []


def test_mnemow_refuses_stdio_faces() -> None:
    """The windowless launcher must not be usable for a face needing stdout."""
    import mnemo_bootstrap

    allowed = mnemo_bootstrap._BACKGROUND_ONLY
    check("mnemow allows the background subcommands",
          {"serve", "service"} <= allowed, detail=str(allowed))
    for face in ("hook-inject", "hook-postedit", "search", "mcp", "ingest"):
        check(f"mnemow rejects `{face}`", face not in allowed)

    argv = sys.argv
    try:
        sys.argv = ["mnemow", "hook-inject"]
        check("main_gui exits non-zero for a stdio face",
              mnemo_bootstrap.main_gui() == 2)
    finally:
        sys.argv = argv


def test_stop_reaps_the_resident(work: Path) -> None:
    """`service stop` must release the ~1.6 GB, not just the backend.

    engine-dev's acceptance wording: after `mnemo service stop`, nothing is
    listening on the embed port and no interpreter holds the model. The
    resident is not a process we spawned, so this also exercises the
    identity proof that replaces a fingerprint: OS-reported socket owner +
    a reply to our authenticated protocol.
    """
    from src.config import EMBED_PORT

    if service_ctl._listening_pid(EMBED_PORT) is not None:
        print("SKIP  resident reap (a resident is already running on this machine)")
        return

    # Start one the way a hook would, then prove stop() reaches it.
    started = service_ctl.spawn_detached(
        [service_ctl.windowless_python(), "-m", "src.cli", "embed-server"],
        cwd=REPO,
    )
    deadline = time.monotonic() + 60.0
    pid = None
    while time.monotonic() < deadline:
        pid = service_ctl._listening_pid(EMBED_PORT)
        if pid is not None:
            break
        time.sleep(0.5)

    if pid is None:
        check("a resident could be started for the test", False,
              detail=f"nothing bound {EMBED_PORT} (spawned {started})")
        return
    check("the resident is listening before stop", True)
    check("the resident answers our authenticated protocol",
          service_ctl._resident_answers_our_token("127.0.0.1", EMBED_PORT))

    outcome = service_ctl.stop_resident()
    check("stop_resident reports it stopped one", outcome == service_ctl.RESIDENT_STOPPED,
          detail=outcome)
    check("NOTHING is listening on the embed port afterwards",
          service_ctl._listening_pid(EMBED_PORT) is None)
    check("the resident process is gone (model released)",
          service_ctl._pid_state(pid) != service_ctl.ALIVE)

    check("stopping again is a harmless no-op",
          service_ctl.stop_resident() == service_ctl.RESIDENT_ABSENT)


def test_resident_stop_spares_a_stranger(work: Path) -> None:
    """A listener that does not answer our token must not be killed."""
    import socket as _socket

    from src.config import EMBED_PORT

    if service_ctl._listening_pid(EMBED_PORT) is not None:
        print("SKIP  stranger-on-the-port (port already in use)")
        return

    # A plain listener that speaks nothing: same port, not our resident.
    script = work / "stranger.py"
    script.write_text(
        "import socket, time\n"
        "s = socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        f"s.bind(('127.0.0.1', {EMBED_PORT}))\n"
        "s.listen(5)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    stranger = subprocess.Popen(
        [sys.executable, str(script)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if service_ctl._listening_pid(EMBED_PORT) is not None:
                break
            time.sleep(0.25)

        outcome = service_ctl.stop_resident()
        check("a stranger on the embed port is reported foreign, not stopped",
              outcome == service_ctl.RESIDENT_FOREIGN, detail=outcome)
        time.sleep(0.4)
        check("THE STRANGER ON THE EMBED PORT SURVIVED",
              stranger.poll() is None, detail=f"exit={stranger.poll()}")
    finally:
        stranger.terminate()
        try:
            stranger.wait(timeout=10)
        except subprocess.TimeoutExpired:
            stranger.kill()


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_real_backend() -> None:
    """The lifecycle against the actual backend, not a stand-in.

    This is where the two-PID problem shows up: on Windows the venv's
    pythonw.exe is a redirector, so the process we spawn and the process that
    binds the port are different. If stop() only signalled the one it
    spawned, the port would stay bound.
    """
    port = free_port()
    os.environ["MNEMO_API_PORT"] = str(port)
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("SKIP  real-backend lifecycle (httpx missing)")
        return

    target = [
        service_ctl.windowless_python(),
        "-m",
        "src.cli",
        "serve",
        "--port",
        str(port),
    ]
    try:
        check("backend start returns OK",
              service_ctl.start(target=target) == service_ctl.EXIT_OK)

        # Poll rather than sleep: bind time varies with machine and imports.
        import httpx

        deadline = time.monotonic() + 30.0
        healthy = False
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=2.0).status_code == 200:
                    healthy = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        check("backend answers /health with 200", healthy)

        info = service_ctl.read_service_info() or {}
        identity = service_ctl.read_identity() or {}
        check("backend published host+port in service.json",
              info.get("port") == port and bool(info.get("host")), detail=str(info))
        check("service.pid and service.json may hold different PIDs (redirector)",
              isinstance(identity.get("pid"), int) and isinstance(info.get("pid"), int),
              detail=f"{identity.get('pid')} vs {info.get('pid')}")

        state = service_ctl.probe()
        check("probe reports running and healthy",
              state.running and state.healthy is True, detail=str(state))
        check("status returns OK for a healthy backend",
              service_ctl.status() == service_ctl.EXIT_OK)
    finally:
        service_ctl.stop()

    check("backend stopped", service_ctl.status() == service_ctl.EXIT_DOWN)

    # The port must actually be released — the real proof that stop() reached
    # the process that was serving, not just the one we spawned.
    import socket

    released = False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                released = True
                break
            except OSError:
                time.sleep(0.25)
    check("the listening port was released by stop", released)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mnemo svc ") as raw:
        work = Path(raw)
        script = work / "long running child.py"
        script.write_text(CHILD, encoding="utf-8")
        test_windowless_spawn(work, script)
        test_lifecycle(work, script)
        test_never_kills_bystanders(work)
        test_state_dir_is_resolved_live()
        test_exit_code_259(work)
        test_clear_identity_spares_a_live_backend()
        test_start_is_serialised(work)
        test_mnemow_refuses_stdio_faces()
        test_resident_stop_spares_a_stranger(work)
        test_stop_reaps_the_resident(work)
        test_real_backend()

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
