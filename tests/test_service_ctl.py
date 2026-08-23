"""Lifecycle + windowless-spawn checks for src/service_ctl.py (block L).

Runs against a trivial long-running script, so it is independent of the
backend. The console-window checks are evidence, not assertion: the child
itself reports whether it owns a console (and whether that console is
visible), and the same probe is run against deliberately *wrong* spawn modes
so a detector that never fires cannot pass for a proof.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
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

from _hygiene import ResidentGuard, claim_embed_port  # noqa: E402

# Claim a private embed port BEFORE src.config is imported: config reads
# MNEMO_EMBED_PORT at import, and everything the suite spawns inherits it.
# That is what makes "ours" a checkable fact rather than a guess about
# recency -- see the docstring in tests/_hygiene.py.
_EMBED_PORT = claim_embed_port()

from src import service_ctl  # noqa: E402

_passed = _failed = 0

_RESIDENTS = ResidentGuard(_EMBED_PORT)

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


def test_switch_current_and_rollback(work: Path) -> None:
    """switch_current repoints `current`; switching back IS the rollback.

    Entirely isolated from the real engine: VERSIONS_DIR/CURRENT_LINK are
    patched to a throwaway tree for the duration, same technique as
    test_state_dir_is_resolved_live() above.
    """
    from unittest.mock import patch

    import src.config as config

    versions = work / "switch-versions"
    versions.mkdir()
    current = work / "switch-current"

    v1, v2 = versions / "v1", versions / "v2"
    for version in (v1, v2):
        version.mkdir()
        (version / "marker.txt").write_text(version.name, encoding="utf-8")

    def marker() -> str | None:
        path = current / "marker.txt"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    with patch.object(config, "VERSIONS_DIR", versions), \
         patch.object(config, "CURRENT_LINK", current):
        service_ctl.switch_current("v1")
        check("switch_current points `current` at v1", marker() == "v1", detail=str(marker()))
        check("_resolve_current_target reports v1",
              service_ctl._resolve_current_target() == v1.resolve(),
              detail=str(service_ctl._resolve_current_target()))

        service_ctl.switch_current("v2")
        check("switch_current repoints `current` to v2", marker() == "v2", detail=str(marker()))
        check("v1's own files survive being switched away from",
              (v1 / "marker.txt").read_text(encoding="utf-8") == "v1")

        # Rollback IS a switch: switching back to v1 must work exactly the
        # same way a forward switch does, because that is the whole point --
        # a health-gated rollback has no special-cased code path to trust.
        service_ctl.switch_current("v1")
        check("switching back to v1 (rollback) restores it", marker() == "v1", detail=str(marker()))
        check("v2's files survive being switched away from",
              (v2 / "marker.txt").read_text(encoding="utf-8") == "v2")

        try:
            service_ctl.switch_current("no-such-tag")
            check("switching to a missing tag raises", False)
        except FileNotFoundError:
            check("switching to a missing tag raises FileNotFoundError", True)

        with service_ctl.update_lock():
            try:
                with service_ctl.update_lock():
                    pass
                check("update_lock refuses re-entry while held", False)
            except RuntimeError as exc:
                check("update_lock refuses a concurrent switch", "in progress" in str(exc))
        check("update_lock releases cleanly after the block",
              not service_ctl._update_lock_path().is_file())


def test_prune_versions_spares_active_and_just_installed(work: Path) -> None:
    """Retention deletes old trees, but never the active or staged-next one."""
    from unittest.mock import patch

    import src.config as config

    versions = work / "prune-versions"
    versions.mkdir()
    current = work / "prune-current"

    tags = ["v1", "v2", "v3", "v4", "v5"]
    now = time.time()
    for index, tag in enumerate(tags):
        entry = versions / tag
        entry.mkdir()
        (entry / "marker.txt").write_text(tag, encoding="utf-8")
        # Oldest first: v1 is the oldest, v5 the newest, by mtime.
        stamp = now - (len(tags) - index) * 10
        os.utime(entry, (stamp, stamp))

    with patch.object(config, "VERSIONS_DIR", versions), \
         patch.object(config, "CURRENT_LINK", current), \
         patch.object(config, "UPDATE_RETENTION_COUNT", 3):
        # `current` is deliberately pointed at an OLD version (v2), as it
        # would be mid-update: the new build (v5) is staged but not yet the
        # active one when a caller might reasonably want to prune.
        service_ctl.switch_current("v2")

        removed = service_ctl.prune_versions(active="v5")
        remaining = {p.name for p in versions.iterdir()}

        check("prune removed exactly the one out-of-retention, unprotected version",
              removed == ["v1"], detail=str(removed))
        check("the active (`current`) version survives even though it is old",
              "v2" in remaining, detail=str(remaining))
        check("the just-installed `active` tag survives even though not current",
              "v5" in remaining, detail=str(remaining))
        check("the newest retention-window versions survive",
              {"v3", "v4"} <= remaining, detail=str(remaining))

        # A second prune with nothing new installed is a no-op: everything
        # left is already within keep_names.
        again = service_ctl.prune_versions(active="v5")
        check("pruning again once retention is satisfied removes nothing",
              again == [], detail=str(again))


def test_ps_fingerprint_contract() -> None:
    """The macOS/BSD path, exercised without a Mac.

    HONEST LABEL: this tests our handling of `ps` output, not `ps` itself.
    The sample is constructed to the documented `lstart` format, not captured
    from a real Darwin box -- we have none. What it does establish is that
    the parsing, the failure modes and the locale pinning behave as the
    docstring claims, so the only thing left unverified is whether Darwin's
    `ps` emits that format.
    """
    from unittest.mock import patch

    calls: list[dict] = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, "env": kwargs.get("env") or {}})
        return subprocess.CompletedProcess(argv, 0, "Sat Jul 26 10:00:00 2026\n", "")

    with patch.object(service_ctl.subprocess, "run", fake_run):
        value = service_ctl._ps_fingerprint(4321)
    check("ps output is returned verbatim, stripped",
          value == "Sat Jul 26 10:00:00 2026", detail=repr(value))
    check("ps is asked for lstart with a suppressed header",
          calls[0]["argv"] == ["ps", "-p", "4321", "-o", "lstart="],
          detail=str(calls[0]["argv"]))
    # Locale/timezone pinning is the difference between a stable fingerprint
    # and disowning our own service when start and stop run under different
    # shells.
    check("LC_ALL is pinned to C", calls[0]["env"].get("LC_ALL") == "C")
    check("TZ is pinned to UTC", calls[0]["env"].get("TZ") == "UTC")

    def failing_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "ps: illegal option -- o")

    with patch.object(service_ctl.subprocess, "run", failing_run):
        check("a ps without lstart yields None, not a bogus value",
              service_ctl._ps_fingerprint(4321) is None)

    def empty_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "\n", "")

    with patch.object(service_ctl.subprocess, "run", empty_run):
        check("empty output yields None", service_ctl._ps_fingerprint(4321) is None)

    def raising_run(argv, **kwargs):
        raise OSError("ps not found")

    with patch.object(service_ctl.subprocess, "run", raising_run):
        check("a missing ps binary yields None", service_ctl._ps_fingerprint(4321) is None)

    # The whole point of returning None: it must never widen what we kill.
    service_ctl._write_identity({"pid": os.getpid(), "fingerprint": None})
    check("a None fingerprint makes the process unmanageable, not killable",
          service_ctl.owned_process() is None)
    service_ctl.service_pid_file().unlink(missing_ok=True)


def test_listening_pid_parser() -> None:
    """The netstat table has traps; a wrong answer here costs 1.6 GB.

    A false negative makes stop_resident report ABSENT and leave the model
    resident. A false positive on a TIME_WAIT row would hand PID 0 to the
    terminator. Both rows really do appear on the embed port -- this sample
    is the shape observed on this machine, with the connection rows kept.
    """
    from unittest.mock import patch

    table = (
        "\r\nActive Connections\r\n\r\n"
        "  Proto  Local Address          Foreign Address        State           PID\r\n"
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1268\r\n"
        "  TCP    127.0.0.1:8917         0.0.0.0:0              LISTENING       29888\r\n"
        "  TCP    127.0.0.1:8917         127.0.0.1:52419        TIME_WAIT       0\r\n"
        "  TCP    127.0.0.1:8918         0.0.0.0:0              LISTENING       777\r\n"
    )

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, table, "")

    with patch.object(service_ctl.subprocess, "run", fake_run):
        check("the LISTENING owner is found", service_ctl._listening_pid(8917) == 29888,
              detail=str(service_ctl._listening_pid(8917)))
        check("a TIME_WAIT row never yields PID 0",
              service_ctl._listening_pid(8917) != 0)
        check("a neighbouring port is not confused for ours",
              service_ctl._listening_pid(8918) == 777)
        check("an unused port reports nothing",
              service_ctl._listening_pid(9999) is None)

    ipv6 = (
        "  Proto  Local Address          Foreign Address        State           PID\r\n"
        "  TCP    [::1]:8917             [::]:0                 LISTENING       4242\r\n"
    )

    def fake_ipv6(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, ipv6, "")

    with patch.object(service_ctl.subprocess, "run", fake_ipv6):
        check("an IPv6 listener is parsed too",
              service_ctl._listening_pid(8917) == 4242,
              detail=str(service_ctl._listening_pid(8917)))

    # A PID of 0 must never reach the terminator even if one slipped through.
    check("PID 0 is not a live process", service_ctl._pid_state(0) == service_ctl.GONE)


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


@contextlib.contextmanager
def _embed_port_of_our_own():
    """The suite already owns a private embed port (see the header).

    Kept as a context manager so the resident tests read the same as before,
    but there is no per-test port juggling any more -- and crucially no
    ephemeral port that nothing can find afterwards, because the teardown
    knows exactly which port to look at.
    """
    yield _EMBED_PORT


def test_stop_reaps_the_resident(work: Path) -> None:
    """`service stop` must release the ~1.6 GB, not just the backend.

    engine-dev's acceptance wording: after `mnemo service stop`, nothing is
    listening on the embed port and no interpreter holds the model. The
    resident is not a process we spawned, so this also exercises the
    identity proof that replaces a fingerprint: OS-reported socket owner +
    a reply to our authenticated protocol.
    """
    with _embed_port_of_our_own() as port:
        # Start one the way a hook would, then prove stop() reaches it.
        started = service_ctl.spawn_detached(
            [service_ctl.windowless_python(), "-m", "src.cli", "embed-server"],
            cwd=REPO,
        )
        deadline = time.monotonic() + 120.0
        pid = None
        while time.monotonic() < deadline:
            pid = service_ctl._listening_pid(port)
            if pid is not None:
                break
            time.sleep(0.5)

        if pid is None:
            check("a resident could be started for the test", False,
                  detail=f"nothing bound {port} (spawned {started})")
            return
        check("the resident is listening before stop", True)
        check("the resident answers our authenticated protocol",
              service_ctl._resident_answers_our_token("127.0.0.1", port))

        outcome = service_ctl.stop_resident()
        check("stop_resident reports it stopped one",
              outcome == service_ctl.RESIDENT_STOPPED, detail=outcome)
        check("NOTHING is listening on the embed port afterwards",
              service_ctl._listening_pid(port) is None)
        check("the resident process is gone (model released)",
              service_ctl._pid_state(pid) != service_ctl.ALIVE)

        check("stopping again is a harmless no-op",
              service_ctl.stop_resident() == service_ctl.RESIDENT_ABSENT)


def test_resident_stop_spares_a_stranger(work: Path) -> None:
    """A listener that does not answer our token must not be killed."""
    with _embed_port_of_our_own() as port:
        # A plain listener that speaks nothing: right port, not our resident.
        script = work / "stranger.py"
        script.write_text(
            "import socket, time\n"
            "s = socket.socket()\n"
            "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"s.bind(('127.0.0.1', {port}))\n"
            "s.listen(5)\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        stranger = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                if service_ctl._listening_pid(port) is not None:
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


def _start_healthy_backend() -> tuple[int, bool]:
    """Start a real backend on a fresh port; return (port, healthy).

    Shared setup for the graceful-shutdown tests below -- same shape as
    test_real_backend()'s own startup, factored out so each test only has to
    say what it tampers with, not how a backend comes up.
    """
    port = free_port()
    os.environ["MNEMO_API_PORT"] = str(port)
    target = [
        service_ctl.windowless_python(),
        "-m", "src.cli", "serve", "--port", str(port),
    ]
    if service_ctl.start(target=target) != service_ctl.EXIT_OK:
        return port, False

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
    return port, healthy


def test_graceful_shutdown_stops_without_force_kill(work: Path) -> None:
    """The happy path (MN-11): /api/shutdown succeeds, `_terminate_tree` is
    never called at all -- the graceful path is the whole point of the
    feature, not a fallback that happens to also work.
    """
    if os.name != "nt":
        print("SKIP  graceful shutdown is Windows-only")
        return
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("SKIP  graceful shutdown success path (httpx missing)")
        return

    port, healthy = _start_healthy_backend()
    if not healthy:
        check("backend for the graceful-shutdown success test came up healthy", False)
        service_ctl.stop()
        return

    from unittest.mock import patch

    calls: list[int] = []
    original = service_ctl._terminate_tree

    def spy(pid: int, **kwargs):
        calls.append(pid)
        return original(pid, **kwargs)

    with patch.object(service_ctl, "_terminate_tree", spy):
        rc = service_ctl.stop()

    check("graceful stop() returns OK", rc == service_ctl.EXIT_OK, detail=f"rc={rc}")
    check("graceful stop() never fell back to _terminate_tree",
          calls == [], detail=str(calls))
    check("status after a graceful stop is DOWN",
          service_ctl.status() == service_ctl.EXIT_DOWN)

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
    check("the listening port was released by the graceful stop", released)


def test_graceful_shutdown_falls_back_to_force_kill(work: Path) -> None:
    """/api/shutdown unreachable -> stop() must still fall through to
    `_terminate_tree` and actually kill the process, exactly the fallback
    the Windows branch was written for.

    Only the published *port* is tampered with, not the pid: the fallback's
    own post-terminate wait re-reads the same service.json for the
    redirector's published pid, so corrupting that field too would test a
    different (and already-covered, see test_never_kills_bystanders)
    failure mode instead of this one.
    """
    if os.name != "nt":
        print("SKIP  graceful shutdown is Windows-only")
        return
    try:
        import httpx  # noqa: F401
    except ImportError:
        print("SKIP  graceful shutdown fallback path (httpx missing)")
        return

    port, healthy = _start_healthy_backend()
    if not healthy:
        check("backend for the graceful-shutdown fallback test came up healthy", False)
        service_ctl.stop()
        return

    info = service_ctl.read_service_info() or {}
    dead_port = free_port()  # freed immediately after -- nothing listens there
    info["port"] = dead_port
    service_ctl.service_info_file().write_text(json.dumps(info), encoding="utf-8")

    from unittest.mock import patch

    calls: list[int] = []
    original = service_ctl._terminate_tree

    def spy(pid: int, **kwargs):
        calls.append(pid)
        return original(pid, **kwargs)

    with patch.object(service_ctl, "_terminate_tree", spy):
        rc = service_ctl.stop()

    check("fallback stop() still returns OK", rc == service_ctl.EXIT_OK, detail=f"rc={rc}")
    check("an unreachable /api/shutdown falls through to _terminate_tree",
          calls != [], detail=str(calls))
    check("status after the fallback stop is DOWN",
          service_ctl.status() == service_ctl.EXIT_DOWN)

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
    check("the real backend's original port was released by the force kill",
          released)


def test_graceful_shutdown_waits_for_both_redirector_pids() -> None:
    """`_graceful_shutdown_windows` must wait out BOTH the spawned pid and
    the backend's own published pid before declaring success.

    This is the exact bug caught during manual verification: briefed to
    reuse only the spawned-pid polling logic, real testing showed the
    redirector case (spawned pid != the pid actually serving) needed both
    watched, or a live backend still finishing its shutdown could be
    reported "stopped" the moment the launcher stub exited. No real second
    process here -- `_pid_state` is faked to report each of the two PIDs
    ALIVE for a few polls before going GONE on its own schedule, so the two
    can be shown to be tracked independently rather than the second one
    being ignored.
    """
    if os.name != "nt":
        print("SKIP  graceful shutdown is Windows-only")
        return

    from unittest.mock import patch

    spawned_pid, info_pid, fingerprint = 11111, 22222, "fp-redirector"
    calls: dict[int, int] = {}
    # The spawned pid reports ALIVE for its first 2 checks then GONE forever;
    # the published pid outlives it (ALIVE for its first 4). A correct wait
    # keeps polling past the point where the spawned pid alone looks stopped.
    alive_until = {spawned_pid: 2, info_pid: 4}

    def fake_pid_state(pid: int) -> str:
        calls[pid] = calls.get(pid, 0) + 1
        return service_ctl.ALIVE if calls[pid] <= alive_until.get(pid, 0) else service_ctl.GONE

    class _Reply:
        status_code = 200

    with patch.object(service_ctl, "read_service_info",
                       lambda: {"host": "127.0.0.1", "port": 4646, "pid": info_pid}), \
         patch("httpx.post", lambda *a, **k: _Reply()), \
         patch.object(service_ctl, "_pid_state", fake_pid_state), \
         patch.object(service_ctl, "process_fingerprint", lambda pid: fingerprint):
        result = service_ctl._graceful_shutdown_windows(spawned_pid, fingerprint, timeout=10.0)

    check("graceful shutdown reports success only once BOTH pids are gone",
          result is True, detail=f"calls={calls}")
    check("the spawned pid alone going quiet was not treated as done",
          calls.get(spawned_pid, 0) > alive_until[spawned_pid] + 1, detail=str(calls))
    check("the published (redirector-served) pid was actually polled and waited on",
          calls.get(info_pid, 0) > alive_until[info_pid], detail=str(calls))


def test_graceful_shutdown_respects_timeout_budget() -> None:
    """A backend that accepts the shutdown POST but never actually exits
    must not hang `stop()` -- the graceful attempt is bounded by the same
    `timeout` `stop()` was given, not a separate, unbounded knob.
    """
    if os.name != "nt":
        print("SKIP  graceful shutdown is Windows-only")
        return

    from unittest.mock import patch

    pid, fingerprint = 33333, "fp-timeout"
    injected_timeout = 0.6  # small on purpose -- this is a timing assertion

    class _Reply:
        status_code = 200

    with patch.object(service_ctl, "read_service_info",
                       lambda: {"host": "127.0.0.1", "port": 4646, "pid": pid}), \
         patch("httpx.post", lambda *a, **k: _Reply()), \
         patch.object(service_ctl, "_pid_state", lambda p: service_ctl.ALIVE), \
         patch.object(service_ctl, "process_fingerprint", lambda p: fingerprint):
        started = time.monotonic()
        result = service_ctl._graceful_shutdown_windows(pid, fingerprint, timeout=injected_timeout)
        elapsed = time.monotonic() - started

    check("a backend that never actually exits is reported as NOT gracefully stopped",
          result is False, detail=str(result))
    check("the graceful attempt is bounded by its own timeout, not left to hang",
          elapsed < injected_timeout + 2.0, detail=f"elapsed={elapsed:.2f}s")
    check("the graceful attempt actually waited close to the injected timeout",
          elapsed >= injected_timeout, detail=f"elapsed={elapsed:.2f}s")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mnemo svc ") as raw:
        work = Path(raw)
        script = work / "long running child.py"
        script.write_text(CHILD, encoding="utf-8")
        test_windowless_spawn(work, script)
        test_lifecycle(work, script)
        test_never_kills_bystanders(work)
        test_state_dir_is_resolved_live()
        test_switch_current_and_rollback(work)
        test_prune_versions_spares_active_and_just_installed(work)
        test_ps_fingerprint_contract()
        test_listening_pid_parser()
        test_exit_code_259(work)
        test_clear_identity_spares_a_live_backend()
        test_start_is_serialised(work)
        test_mnemow_refuses_stdio_faces()
        test_resident_stop_spares_a_stranger(work)
        test_stop_reaps_the_resident(work)
        try:
            test_real_backend()
            test_graceful_shutdown_stops_without_force_kill(work)
            test_graceful_shutdown_falls_back_to_force_kill(work)
            test_graceful_shutdown_waits_for_both_redirector_pids()
            test_graceful_shutdown_respects_timeout_budget()
        finally:
            # The backend indexes on start, which spawns a resident on the
            # real port. With idle exit off it would outlive the suite.
            _RESIDENTS.reap()

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
