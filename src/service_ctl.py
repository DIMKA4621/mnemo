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

**A bare PID is never grounds to kill anything.** PIDs are reused, so a stale
state file naming a recycled PID names somebody else's process — an editor,
a build. Termination is gated on ``owned_process()``: the PID must come from
``service.pid`` (the file only this module writes) *and* still carry the
creation-time fingerprint recorded when we spawned it. The backend's
``service.json`` has no fingerprint and another writer, so it is used for
reporting and for noticing a backend we did not start — never as a licence to
terminate. A service somebody else launched is refused, not killed.

Two deliberate limits, stated rather than implied:

* **Same user only.** The service runs as the logged-in user (Task Scheduler
  registers it under that principal); cross-principal operation is not
  supported. A PID we may not open is reported ``foreign`` and never touched.
* **macOS is unverified.** ``_ps_fingerprint`` is written to the documented
  ``ps`` behaviour but has not been run on a Mac — we have no machine.

``start(foreground=True)`` writes no state file: it is a debugging mode, so
the service is intentionally invisible to ``status``/``stop`` (Ctrl-C is the
control).

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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import config
from .config import (
    SERVICE_READY_TIMEOUT,
    SERVICE_START_GRACE,
    SERVICE_STOP_TIMEOUT,
)


def state_dir() -> Path:
    """The writable state directory, resolved when asked.

    Never ``from .config import STATE_DIR``: that binds the Path once at
    import, so a test or a container that repoints ``config.STATE_DIR``
    afterwards would leave this module reading and writing the old
    directory. Same value, computed live -- as api.service_info_file does.
    """
    return Path(config.STATE_DIR)


def service_pid_file() -> Path:
    """Our spawn-identity file. Derived live, see state_dir()."""
    return state_dir() / "service.pid"


def service_info_file() -> Path:
    """The backend's service.json. Derived live, see state_dir()."""
    return state_dir() / "service.json"

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
    """macOS / generic POSIX fallback: the start time as ``ps`` reports it.

    **Unverified on a real Mac — we have no machine.** What follows is
    reasoning from the documented ``ps`` contract, not a measurement, and it
    is written out so the next person can check it rather than re-derive it.

    * ``lstart`` is a BSD/Darwin (and procps) extension, *not* in the POSIX
      ``-o`` keyword set. Where it is missing, ``ps`` fails and we return
      None. That degrades safely: a None fingerprint makes ``owned_process``
      refuse, so the worst case is "cannot manage this service", never
      "killed the wrong process". ``start`` says so out loud when it happens.
    * There is deliberately no POSIX fallback. The portable keyword is
      ``etime`` (elapsed time), which changes between readings and so cannot
      serve as an equality check — an unstable fingerprint is worse than
      none, because it fails in the direction of not recognising our own
      process.
    * ``LC_ALL``/``TZ`` are pinned because ``lstart`` is a *formatted local
      date*. Two readings of the same process under different locales or
      timezones would render differently and compare unequal — start under
      one shell, stop under another, and we would disown our own service.
      Pinning both makes the string depend only on the process.
    * ``-o lstart=`` with an empty header suppresses the column heading
      (POSIX behaviour for ``-o keyword=header``), so stdout is the value
      alone.
    * Resolution is one second, against 100 ns on Windows and clock ticks on
      Linux. PID reuse *within the same second* would collide; that is the
      documented weak spot of this platform's path.
    """
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=5,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None  # no such process, or a ps without `lstart`
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


# Liveness is tri-state on purpose. "Cannot inspect" is not "not running":
# collapsing the two is how a live service gets reported as stopped and a
# second one gets spawned on top of it.
ALIVE = "alive"
GONE = "gone"
FOREIGN = "foreign"   # exists, but owned by another user / higher integrity


def _pid_state(pid: int) -> str:
    """ALIVE / GONE / FOREIGN for one PID.

    Windows liveness is ``WaitForSingleObject(handle, 0)``, never the exit
    code: ``GetExitCodeProcess`` reports STILL_ACTIVE (259) for a running
    process *and* for one that genuinely exited with 259, so a backend
    returning 259 would be "running" forever.
    """
    if pid <= 0:
        return GONE
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # SYNCHRONIZE (to wait) | PROCESS_QUERY_LIMITED_INFORMATION.
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            # 5 = ERROR_ACCESS_DENIED: the PID exists, we may not touch it.
            return FOREIGN if ctypes.get_last_error() == 5 else GONE
        try:
            # WAIT_TIMEOUT (258) = still running; WAIT_OBJECT_0 (0) = exited.
            return ALIVE if kernel32.WaitForSingleObject(handle, 0) == 258 else GONE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return GONE
    except PermissionError:
        return FOREIGN
    return ALIVE


def _pid_alive(pid: int) -> bool:
    """True when the PID is running and inspectable by us."""
    return _pid_state(pid) == ALIVE


def _terminate_tree(pid: int, *, force: bool = False) -> None:
    """Stop a process we have already proven is ours, plus its children.

    The tree matters because of the redirector: on Windows a venv launcher
    spawns the real interpreter as a child, so signalling only the PID we
    recorded can leave the server running and the port bound. The tree is
    taken from the OS (parent -> child), never from a file, so it cannot
    reach a process that merely reuses a number.

    Callers must have gone through owned_process() first -- this function
    does no verification of its own.
    """
    if pid <= 0:
        return
    if os.name == "nt":
        # taskkill /T walks the child list for us. Windowless, like
        # everything else we spawn.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
            timeout=30,
            check=False,
        )
        return

    sig = signal.SIGKILL if force else signal.SIGTERM
    # We spawn with start_new_session, so our child leads its own process
    # group and killpg reaches its children. If it is NOT the leader, the
    # group belongs to somebody else and must not be signalled.
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
            return
    except OSError:
        pass
    try:
        os.kill(pid, sig)
    except OSError:
        pass


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
    return _read_json(service_info_file())


def read_identity() -> dict | None:
    """Our ``service.pid``: who we spawned, and which instance it was."""
    return _read_json(service_pid_file())


def _write_identity(identity: dict[str, Any]) -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    tmp = service_pid_file().with_suffix(".pid.tmp")
    tmp.write_text(json.dumps(identity, indent=2), encoding="utf-8")
    tmp.replace(service_pid_file())  # atomic: never a half-written state file


def _clear_identity() -> None:
    """Drop our own state file, and only our own.

    ``service.json`` belongs to the backend. It is removed only when the
    process it names is verifiably gone -- otherwise clearing a stale entry
    of ours would delete the state file of a healthy backend somebody else
    started.
    """
    try:
        service_pid_file().unlink()
    except OSError:
        pass

    info = read_service_info()
    info_pid = (info or {}).get("pid")
    if info is not None and (
        not isinstance(info_pid, int) or _pid_state(info_pid) == GONE
    ):
        try:
            service_info_file().unlink()
        except OSError:
            pass


@dataclass(frozen=True)
class ServiceState:
    """What we can truthfully say about the service right now."""

    running: bool
    info: dict | None = None
    pid: int | None = None
    stale: bool = False          # a state file was present, its owner is gone
    healthy: bool | None = None  # None = no endpoint recorded yet
    managed: bool = False        # we spawned it and can prove it is the same
                                 # process -- the ONLY case we may terminate
    foreign: bool = False        # exists but belongs to another principal

    @property
    def unhealthy(self) -> bool:
        return self.running and self.healthy is False


def owned_process() -> tuple[int, str] | None:
    """The PID we spawned, proven to still be that same process.

    This is the single gate on termination. It reads ONLY ``service.pid`` --
    the file service_ctl writes and nobody else touches -- and it requires a
    fingerprint that still matches. Anything without a verified fingerprint
    is, by construction, not something we are allowed to kill.
    """
    identity = read_identity()
    if not identity:
        return None
    pid = identity.get("pid")
    fingerprint = identity.get("fingerprint")
    if not isinstance(pid, int) or not isinstance(fingerprint, str) or not fingerprint:
        return None
    _reap(pid)
    if _pid_state(pid) != ALIVE:
        return None
    current = process_fingerprint(pid)
    # A live PID whose creation time differs is a different process that
    # merely inherited the number. Absence of a fingerprint is NOT a pass.
    if current is None or current != fingerprint:
        return None
    return pid, fingerprint


def probe() -> ServiceState:
    """Resolve the real state.

    Liveness comes from ``service.pid`` alone. ``service.json`` is written by
    the backend and carries no fingerprint, so it is used for *reporting*
    (endpoint, version) and to notice a backend we did not start -- never as
    a licence to terminate a PID.
    """
    identity = read_identity()
    info = read_service_info()
    if identity is None and info is None:
        return ServiceState(running=False)

    owned = owned_process()
    if owned is not None:
        return ServiceState(
            running=True, info=info, pid=owned[0], managed=True,
            healthy=_health(info or {}),
        )

    # Not ours (or not provably ours). Is something still serving?
    info_pid = (info or {}).get("pid")
    if isinstance(info_pid, int):
        state = _pid_state(info_pid)
        if state in (ALIVE, FOREIGN):
            return ServiceState(
                running=True, info=info, pid=info_pid, managed=False,
                foreign=state == FOREIGN, healthy=_health(info or {}),
            )

    identity_pid = (identity or {}).get("pid")
    return ServiceState(
        running=False, info=info,
        pid=identity_pid if isinstance(identity_pid, int) else None,
        stale=True,
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


# ------------------------------------------------------------- resident

# The embedding resident is a second process holding ~1.6 GB, and it is NOT
# one we spawn: a hook or a search starts it on demand. So `service stop` has
# to reach a process it has no fingerprint for -- and the rule that a bare PID
# is never grounds to kill still applies.
#
# The proof used instead is stronger than a file: the OS says which PID owns
# the listening socket on the resident's port, and the process on that port
# answers OUR authenticated protocol with this machine's token. A stranger
# that happened to bind the port cannot do the second part. The PID and its
# creation time are re-checked after the probe, so a resident exiting mid-way
# cannot hand the kill to whoever inherits the number.

RESIDENT_STOPPED = "stopped"
RESIDENT_ABSENT = "absent"
RESIDENT_FOREIGN = "foreign"   # alive, but not answering our token
RESIDENT_FAILED = "failed"


def _listening_pid(port: int) -> int | None:
    """PID owning the LISTEN socket on ``port``, straight from the OS."""
    if os.name == "nt":
        out = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, errors="replace",
            creationflags=_CREATE_NO_WINDOW, timeout=30, check=False,
        ).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                local = parts[1]
                if local.rsplit(":", 1)[-1] == str(port) and parts[4].isdigit():
                    return int(parts[4])
        return None

    for argv, extract in (
        (["ss", "-ltnpH"], lambda line: line.split("pid=")[1].split(",")[0]
            if "pid=" in line else None),
        (["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
         lambda line: line.strip() or None),
    ):
        try:
            out = subprocess.run(
                argv, capture_output=True, text=True, errors="replace",
                timeout=30, check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in out.splitlines():
            if argv[0] == "ss" and f":{port} " not in line and not line.endswith(f":{port}"):
                continue
            value = extract(line)
            if value and value.isdigit():
                return int(value)
    return None


def _resident_answers_our_token(host: str, port: int, timeout: float = 2.0) -> bool:
    """True when the listener speaks our protocol AND accepts our token.

    Sends an empty query: the resident replies ``{"error": "empty"}`` without
    embedding anything, so this costs nothing and never loads a model.
    """
    import socket as _socket

    from .embed_server import _recv, _send, _token

    try:
        token = _token()
    except OSError:
        return False
    try:
        with _socket.create_connection((host, port), timeout=timeout) as sock:
            _send(sock, {"token": token, "text": "", "kind": "query"})
            reply = _recv(sock, timeout=timeout)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    # "empty" = authenticated and understood us. "unauthorized" = someone
    # else's resident, which we must not touch.
    return reply.get("error") == "empty"


def stop_resident(*, timeout: float = 10.0) -> str:
    """Stop the embedding resident, if it is ours to stop.

    Without this, `service stop` frees the backend but leaves the process
    that actually holds the ~1.6 GB running with no command that ends it --
    which is worse than a slow cold start, because nothing tells the user.
    """
    from .config import EMBED_HOST, EMBED_HOST_IS_LOCAL, EMBED_PORT

    if not EMBED_HOST_IS_LOCAL:
        # A shared/remote resident belongs to whoever runs that machine.
        return RESIDENT_ABSENT

    pid = _listening_pid(EMBED_PORT)
    if pid is None or _pid_state(pid) != ALIVE:
        return RESIDENT_ABSENT
    before = process_fingerprint(pid)

    if not _resident_answers_our_token(EMBED_HOST, EMBED_PORT):
        return RESIDENT_FOREIGN

    # Re-check: the resident could have exited during the probe and left the
    # port (and the PID) to somebody else.
    if _listening_pid(EMBED_PORT) != pid or process_fingerprint(pid) != before:
        return RESIDENT_ABSENT

    _terminate_tree(pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _pid_state(pid) != ALIVE or process_fingerprint(pid) != before:
            return RESIDENT_STOPPED
        time.sleep(0.1)
    return RESIDENT_FAILED


# ------------------------------------------------------------- lifecycle


def _start_lock() -> Path:
    return state_dir() / "service.start.lock"


@contextmanager
def _exclusive_start():
    """Serialise the whole of start(): probe -> spawn -> record.

    Without it the window between spawning and writing service.pid (a full
    SERVICE_START_GRACE) reads as "stopped", so a second start spawns a
    second backend and only one of them is ever tracked.
    """
    state_dir().mkdir(parents=True, exist_ok=True)
    lock = _start_lock()
    handle = None
    for _ in range(2):
        try:
            handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # Break a lock whose owner died mid-start; never one still held.
            holder = _read_json(lock) or {}
            holder_pid = holder.get("pid")
            if isinstance(holder_pid, int) and _pid_state(holder_pid) == ALIVE:
                raise RuntimeError(
                    f"another `mnemo service start` is running (pid {holder_pid})"
                )
            try:
                lock.unlink()
            except OSError:
                pass
    if handle is None:
        raise RuntimeError("could not acquire the service start lock")
    try:
        os.write(handle, json.dumps({"pid": os.getpid()}).encode("utf-8"))
        os.close(handle)
        yield
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def _handed_off(spawned_pid: int, info: dict) -> bool:
    """True when the PID we spawned was a stub that handed off to a live one.

    This is the redirector rule again, in a new place: on Windows the venv's
    ``pythonw.exe`` is a launcher stub, so the PID returned by the spawn is
    not the PID that ends up serving, and the stub is free to exit once the
    real interpreter is running. Treating that exit as "the service died"
    would kill a healthy backend's state file moments after it came up.

    The backend's own ``service.json`` is what settles it: it is written by
    the process actually serving, so a different, live PID there means the
    handover happened rather than the service failing.
    """
    other = info.get("pid")
    try:
        other_pid = int(other) if other else 0
    except (TypeError, ValueError):
        return False
    if other_pid <= 0 or other_pid == spawned_pid:
        return False
    return _pid_state(other_pid) == ALIVE


def _await_ready(pid: int, timeout: float) -> tuple[bool, bool, str | None]:
    """Wait until the backend answers ``/health``.

    Returns ``(ready, died, endpoint)``. The endpoint stays None while the
    backend has not published one yet, which is a normal early state and not
    a failure: on a cold start the process spends seconds importing FastAPI,
    both MCP faces and the store before it binds a port at all.

    ``/health`` is the authority on readiness, never the liveness of the PID
    we spawned -- see ``_handed_off``. ``died`` is reported only when that PID
    is gone *and* nothing else is serving, which is the one case where waiting
    out the timeout would name the wrong problem.
    """
    deadline = time.monotonic() + timeout
    endpoint: str | None = None
    while time.monotonic() < deadline:
        info = read_service_info() or {}
        host, port = info.get("host"), info.get("port")
        if host and port:
            endpoint = f"http://{host}:{port}"
            if _health(info):
                return True, False, endpoint
        if _pid_state(pid) != ALIVE and not _handed_off(pid, info):
            return False, True, endpoint
        time.sleep(0.25)
    return False, False, endpoint


def start(
    *,
    foreground: bool = False,
    target: Sequence[str] | None = None,
    wait_ready: bool | None = None,
) -> int:
    """Start the service unless it is already running.

    ``foreground=True`` runs the backend in this terminal and writes NO state
    file: it is a debugging mode, deliberately invisible to ``status`` and
    ``stop`` (there is nothing detached to manage -- Ctrl-C is the control).

    ``target`` is an additive convenience over the contract signature: it lets
    the lifecycle be exercised against any long-running command.

    ``wait_ready`` decides whether "started" has to mean "answering". It
    defaults to *whether we know what ready means*: the real backend publishes
    an endpoint and serves ``/health``, an arbitrary ``target`` does neither,
    so waiting on one would be waiting for something that is never coming.
    """
    argv = list(target) if target is not None else _default_target()
    if wait_ready is None:
        wait_ready = target is None
    if foreground:
        return subprocess.call(argv)

    try:
        with _exclusive_start():
            state = probe()
            if state.running:
                owner = "" if state.managed else " (not started by us)"
                print(f"mnemo service: already running (pid {state.pid}){owner}")
                return EXIT_OK
            if state.stale:
                # Contract: an orphaned state file is cleaned up by start.
                _clear_identity()
                print("mnemo service: removed stale process-state files")

            engine_root = Path(__file__).resolve().parent.parent
            pid = spawn_detached(argv, cwd=engine_root)

            # A child that dies instantly (bad interpreter, import error) must
            # not be recorded as a running service.
            deadline = time.monotonic() + SERVICE_START_GRACE
            while time.monotonic() < deadline:
                if _pid_state(pid) != ALIVE:
                    _reap(pid)
                    print("mnemo service: the process exited immediately after start")
                    return EXIT_DOWN
                time.sleep(0.05)

            fingerprint = process_fingerprint(pid)
            if fingerprint is None:
                # Without it `stop` will refuse to touch this process, by
                # design. Say so now rather than at stop time: silently
                # unmanageable is the worst of the three outcomes.
                print(
                    "mnemo service: WARNING - cannot read this platform's "
                    "process start time, so `service stop` will refuse to "
                    f"manage pid {pid}; stop it manually"
                )

            _write_identity(
                {
                    "pid": pid,
                    "fingerprint": fingerprint,
                    "started_at": datetime.now(timezone.utc).astimezone().isoformat(),
                    "python": argv[0],
                    "argv": argv,
                }
            )
            if not wait_ready or SERVICE_READY_TIMEOUT <= 0:
                print(f"mnemo service: started (pid {pid})")
                return EXIT_OK

            # "started" now means "serving". Anything that runs a command
            # straight after this -- the installer's closing `doctor`, a
            # scripted `start && search` -- used to race the bind and report a
            # healthy backend as DOWN.
            ready, died, endpoint = _await_ready(pid, SERVICE_READY_TIMEOUT)
            if ready:
                print(f"mnemo service: started (pid {pid}, {endpoint})")
                return EXIT_OK
            if died:
                _reap(pid)
                _clear_identity()
                print("mnemo service: the process exited while starting")
                return EXIT_DOWN
            where = endpoint or "no endpoint published"
            print(
                f"mnemo service: started (pid {pid}) but not answering at "
                f"{where} after {SERVICE_READY_TIMEOUT:.0f}s"
            )
            print("mnemo service: check `mnemo service status`; it may still be coming up")
            return EXIT_UNHEALTHY
    except RuntimeError as exc:
        print(f"mnemo service: {exc}")
        return EXIT_UNHEALTHY


def stop(*, timeout: float = SERVICE_STOP_TIMEOUT) -> int:
    """Stop the service we started, and verify the process is really gone.

    Refuses to terminate anything it cannot prove is ours. A PID read from a
    file is not proof: ``service.json`` is rewritten by the backend without a
    fingerprint, PIDs are reused, and the file can be stale or hand-edited --
    so a bare PID must never reach TerminateProcess.
    """
    # "stop" means all of mnemo, not just the backend. The resident is a
    # separate process holding the model; leaving it behind would make a
    # successful-looking stop still hold ~1.6 GB.
    resident = stop_resident()
    if resident == RESIDENT_STOPPED:
        print("mnemo service: embedding resident stopped (model released)")
    elif resident == RESIDENT_FOREIGN:
        print("mnemo service: a resident on the embed port is not ours - left alone")

    owned = owned_process()
    if owned is None:
        state = probe()
        if state.running:
            # Something is serving, but it is not a process we can prove we
            # started -- e.g. launched by systemd, Task Scheduler or by hand.
            who = "another principal" if state.foreign else "another launcher"
            print(f"mnemo service: refusing to stop pid {state.pid} - started by {who}")
            print("mnemo service: stop it where it was started, or use its own shutdown")
            return EXIT_UNHEALTHY
        if state.stale:
            _clear_identity()
            print("mnemo service: not running (removed stale process-state files)")
        else:
            print("mnemo service: not running")
        return EXIT_DOWN

    pid, fingerprint = owned
    _terminate_tree(pid)

    # Wait for the published backend PID too, not just the one we spawned:
    # the redirector can be gone while the server it fronted is still exiting,
    # and returning then would let `status` report a service that is on its
    # way out. Waiting on a bare PID is safe -- only *killing* one is not.
    info_pid = (read_service_info() or {}).get("pid")
    waiting = [pid]
    if isinstance(info_pid, int) and info_pid != pid:
        waiting.append(info_pid)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _reap(pid)
        ours_gone = (
            _pid_state(pid) != ALIVE or process_fingerprint(pid) != fingerprint
        )
        if ours_gone and all(_pid_state(other) != ALIVE for other in waiting):
            _clear_identity()
            print(f"mnemo service: stopped (pid {pid})")
            return EXIT_OK
        time.sleep(0.1)

    if os.name != "nt":  # graceful window expired - escalate
        _terminate_tree(pid, force=True)
        time.sleep(0.2)
        _reap(pid)
        if _pid_state(pid) != ALIVE:
            _clear_identity()
            print(f"mnemo service: killed (pid {pid})")
            return EXIT_OK

    print(f"mnemo service: FAILED to stop pid {pid}")
    return EXIT_UNHEALTHY


def status() -> int:
    """Report the truth: running / unhealthy / stopped."""
    state = probe()
    if not state.running:
        if state.stale:
            print("mnemo service: stopped (stale state file - process is gone)")
        else:
            print("mnemo service: stopped")
        return EXIT_DOWN

    info = state.info or {}
    endpoint = (
        f"http://{info['host']}:{info['port']}"
        if info.get("host") and info.get("port")
        else "no endpoint published"
    )
    origin = "" if state.managed else ", not started by us"
    if state.unhealthy:
        print(f"mnemo service: unhealthy (pid {state.pid}, {endpoint}{origin})")
        return EXIT_UNHEALTHY

    started = info.get("started_at") or (read_identity() or {}).get("started_at", "?")
    print(
        f"mnemo service: running (pid {state.pid}, {endpoint}, "
        f"since {started}{origin})"
    )
    _report_resident()
    return EXIT_OK


def _report_resident() -> None:
    """One line about the model holder — the other half of "is mnemo up"."""
    from .config import EMBED_HOST_IS_LOCAL, EMBED_PORT

    if not EMBED_HOST_IS_LOCAL:
        print("mnemo service: embedding resident is remote (not managed here)")
        return
    pid = _listening_pid(EMBED_PORT)
    if pid is None or _pid_state(pid) != ALIVE:
        print("mnemo service: embedding resident not running (model not loaded)")
        return
    print(f"mnemo service: embedding resident running (pid {pid}, port {EMBED_PORT})")


def restart(*, target: Sequence[str] | None = None) -> int:
    """Stop (if up) then start. A stopped service restarts cleanly."""
    stop()
    return start(target=target)
