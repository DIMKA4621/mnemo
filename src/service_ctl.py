"""Process lifecycle for the mnemo service — block L, contracts §11.2.

Two things are hard here, and both are handled below rather than by the
caller:

**Nothing may flash a console window** (NFR-1 — the user's named pain point).
On Windows that is achieved structurally, not by a flag: the child runs under
``pythonw.exe``, a GUI-subsystem binary that *cannot* own a console, with
``CREATE_NO_WINDOW`` as the belt to that braces. Note that ``CREATE_NO_WINDOW``
is documented as **ignored** when combined with ``DETACHED_PROCESS`` or
``CREATE_NEW_CONSOLE``, so the two are never OR-ed together here — a detail
that quietly turns the guard into a no-op if got wrong. On POSIX the child is
put in its own session with stdio on /dev/null.

**A bare PID is not proof the process is ours.** PIDs are reused, so a stale
``service.json`` naming a recycled PID would otherwise report a healthy
service that is really someone else's process. Every liveness answer here is
therefore (pid, creation-time) — a pair the OS cannot recycle.

The module is deliberately usable before the backend exists: ``start`` takes
an arbitrary target command, so the whole lifecycle is testable against a
trivial script.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import (
    SERVICE_INFO_FILE,
    SERVICE_PID_FILE,
    SERVICE_START_GRACE,
    SERVICE_STOP_TIMEOUT,
    STATE_DIR,
)

# Exit codes. 3 means "service is down" across the whole CLI (contracts
# §11.1), so a script can tell it apart from "ran fine, found nothing".
EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_DOWN = 3

# Windows creation flags. Spelled out rather than taken from subprocess so
# the module imports cleanly on POSIX, where those attributes do not exist.
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000
_STILL_ACTIVE = 259


# ------------------------------------------------------- process identity


def _windows_fingerprint(pid: int) -> str | None:
    """Creation time of ``pid`` as reported by GetProcessTimes."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # PROCESS_QUERY_LIMITED_INFORMATION: enough for times, and it is granted
    # for processes we could not fully open (e.g. elevated ones).
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return f"{creation.dwHighDateTime}:{creation.dwLowDateTime}"
    finally:
        kernel32.CloseHandle(handle)


def _linux_fingerprint(pid: int) -> str | None:
    """Field 22 of /proc/<pid>/stat — start time in clock ticks since boot."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # The comm field is parenthesised and may itself contain spaces, so split
    # after the last ')' instead of naively on whitespace.
    tail = raw.rpartition(")")[2].split()
    if len(tail) < 20:
        return None
    return tail[19]  # stat field 22, zero-based within the post-comm tail


def _ps_fingerprint(pid: int) -> str | None:
    """macOS / generic POSIX fallback: the start time as ps reports it."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value or None


def process_fingerprint(pid: int) -> str | None:
    """Identify one *instance* of a PID, so PID reuse cannot fool us.

    Returns None when the process does not exist (or cannot be inspected),
    which callers treat as "not running".
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_fingerprint(pid)
    if sys.platform.startswith("linux"):
        return _linux_fingerprint(pid)
    return _ps_fingerprint(pid)


def _pid_alive(pid: int) -> bool:
    """True when *some* process currently holds this PID."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _reap(pid: int) -> None:
    """Clear a zombie if the process happens to be our own child.

    Only relevant when start() and stop() run inside one long-lived process
    (the tests do exactly that): on POSIX an unreaped child stays visible to
    kill(0) forever and would be reported as running after it exited.
    """
    if os.name == "nt":
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        pass


# ------------------------------------------------------------- state file


def _read_json(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def read_service_info() -> dict | None:
    """The backend's ``service.json`` (contracts §11.2), or None."""
    return _read_json(SERVICE_INFO_FILE)


def read_identity() -> dict | None:
    """Our ``service.pid``: who we spawned, and which instance it was."""
    return _read_json(SERVICE_PID_FILE)


def _write_identity(identity: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SERVICE_PID_FILE.with_suffix(".pid.tmp")
    tmp.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    tmp.replace(SERVICE_PID_FILE)  # atomic: never a half-written state file


def _clear_identity() -> None:
    """Drop our own file. ``service.json`` belongs to the backend — but a
    force-killed backend cannot clean up after itself, so an orphan is
    removed too (only ever when its process is verified gone)."""
    for path in (SERVICE_PID_FILE, SERVICE_INFO_FILE):
        try:
            path.unlink()
        except OSError:
            pass


@dataclass(frozen=True)
class ServiceState:
    """What we can truthfully say about the service right now."""

    running: bool
    info: dict | None = None
    pid: int | None = None
    stale: bool = False   # a state file was present but its owner is gone
    healthy: bool | None = None  # None = no endpoint recorded yet

    @property
    def unhealthy(self) -> bool:
        return self.running and self.healthy is False


def probe() -> ServiceState:
    """Resolve the real state: state file + live process + optional health."""
    info = read_service_info()
    if info is None:
        return ServiceState(running=False)

    pid = info.get("pid")
    if not isinstance(pid, int):
        return ServiceState(running=False, info=info, stale=True)

    _reap(pid)
    recorded = info.get("fingerprint")
    current = process_fingerprint(pid)
    # Both halves must agree. A live PID whose creation time differs is a
    # different process that merely inherited the number.
    if current is None or not _pid_alive(pid):
        return ServiceState(running=False, info=info, pid=pid, stale=True)
    if recorded is not None and current != recorded:
        return ServiceState(running=False, info=info, pid=pid, stale=True)

    return ServiceState(
        running=True, info=info, pid=pid, healthy=_health(info)
    )


def _health(info: dict) -> bool | None:
    """GET /health, when the backend has recorded where it listens.

    None means "not applicable yet" — the process is up but has not published
    an endpoint (or is not the backend at all, as in the lifecycle tests).
    """
    host, port = info.get("host"), info.get("port")
    if not host or not port:
        return None
    import httpx

    try:
        reply = httpx.get(f"http://{host}:{port}/health", timeout=2.0)
    except httpx.HTTPError:
        return False
    return reply.status_code == 200


# ------------------------------------------------------------- spawning


def _windowless_kwargs() -> dict[str, Any]:
    """Popen kwargs that guarantee a detached, invisible child.

    Windows: CREATE_NO_WINDOW is the only no-console flag that survives here
    — DETACHED_PROCESS would *disable* it (documented behaviour), and
    CREATE_NEW_CONSOLE is exactly what we are avoiding. Detachment comes from
    the GUI-subsystem interpreter plus a fresh process group, not from
    DETACHED_PROCESS.

    POSIX: a new session detaches the child from the controlling terminal, so
    it survives the shell that started it and can never write to its tty.
    """
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def windowless_python() -> str:
    """The interpreter to spawn background children with.

    On Windows that is ``pythonw.exe``: being a GUI-subsystem binary it has no
    console to show, which makes "no window" a property of the executable
    rather than of the caller's flags — the only form of the guarantee that
    also holds when Task Scheduler or a shortcut does the launching.
    """
    executable = Path(sys.executable)
    if os.name != "nt":
        return str(executable)
    candidate = executable.with_name(
        executable.name.replace("python", "pythonw", 1)
    )
    if "pythonw" in executable.name:
        return str(executable)
    return str(candidate) if candidate.is_file() else str(executable)


def spawn_detached(argv: Sequence[str], *, cwd: str | Path | None = None) -> int:
    """Launch ``argv`` windowless and detached; return the child PID."""
    child = subprocess.Popen(  # noqa: S603 - argv is built by us, never a shell
        list(argv),
        cwd=str(cwd) if cwd is not None else None,
        **_windowless_kwargs(),
    )
    return child.pid


def _default_target() -> list[str]:
    """The backend command: `mnemo serve` under the windowless interpreter."""
    return [windowless_python(), "-m", "src.cli", "serve"]


# ------------------------------------------------------------- lifecycle


def start(
    *,
    foreground: bool = False,
    target: Sequence[str] | None = None,
) -> int:
    """Start the service unless it is already running.

    ``target`` is an additive convenience over the contract signature: it lets
    the lifecycle be exercised against any long-running command before the
    backend module exists.
    """
    state = probe()
    if state.running:
        print(f"mnemo service: already running (pid {state.pid})")
        return EXIT_OK
    if state.stale:
        # Contract §11.2: an orphaned state file is cleaned up by start.
        _clear_service_info()
        print("mnemo service: removed stale service.json")

    argv = list(target) if target is not None else _default_target()

    if foreground:
        return subprocess.call(argv)

    engine_root = Path(__file__).resolve().parent.parent
    pid = spawn_detached(argv, cwd=engine_root)

    # A child that dies instantly (bad interpreter, import error) must not be
    # recorded as a running service.
    deadline = time.monotonic() + SERVICE_START_GRACE
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            _reap(pid)
            print("mnemo service: the process exited immediately after start")
            return EXIT_DOWN
        time.sleep(0.05)

    _write_service_info(
        {
            "pid": pid,
            "fingerprint": process_fingerprint(pid),
            "started_at": datetime.now(timezone.utc).astimezone().isoformat(),
            "python": argv[0],
            "argv": argv,
        }
    )
    print(f"mnemo service: started (pid {pid})")
    return EXIT_OK


def stop(*, timeout: float = SERVICE_STOP_TIMEOUT) -> int:
    """Stop the service and verify the process is really gone."""
    state = probe()
    if not state.running:
        if state.stale:
            _clear_service_info()
            print("mnemo service: not running (removed stale service.json)")
        else:
            print("mnemo service: not running")
        return EXIT_DOWN

    pid = int(state.pid or 0)
    fingerprint = (state.info or {}).get("fingerprint")

    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _reap(pid)
        if not _pid_alive(pid) or process_fingerprint(pid) != fingerprint:
            _clear_service_info()
            print(f"mnemo service: stopped (pid {pid})")
            return EXIT_OK
        time.sleep(0.1)

    if os.name != "nt":  # graceful window expired — escalate
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        time.sleep(0.2)
        _reap(pid)
        if not _pid_alive(pid):
            _clear_service_info()
            print(f"mnemo service: killed (pid {pid})")
            return EXIT_OK

    print(f"mnemo service: FAILED to stop pid {pid}")
    return EXIT_UNHEALTHY


def status() -> int:
    """Report the truth: running / unhealthy / stopped."""
    state = probe()
    if not state.running:
        if state.stale:
            print("mnemo service: stopped (stale service.json — process is gone)")
        else:
            print("mnemo service: stopped")
        return EXIT_DOWN

    info = state.info or {}
    endpoint = (
        f"http://{info['host']}:{info['port']}"
        if info.get("host") and info.get("port")
        else "no endpoint published"
    )
    if state.unhealthy:
        print(f"mnemo service: unhealthy (pid {state.pid}, {endpoint})")
        return EXIT_UNHEALTHY

    started = info.get("started_at", "?")
    print(f"mnemo service: running (pid {state.pid}, {endpoint}, since {started})")
    return EXIT_OK


def restart(*, target: Sequence[str] | None = None) -> int:
    """Stop (if up) then start. A stopped service restarts cleanly."""
    stop()
    return start(target=target)
