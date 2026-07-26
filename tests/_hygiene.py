"""Test hygiene: reap the residents THIS suite caused, and nothing else.

``MNEMO_EMBED_IDLE_TIMEOUT`` defaults to 0, so a resident started during a
test never exits by itself and must be reaped. The hard part is not the
reaping, it is knowing which ones are ours.

**The bug this file used to have.** Ownership was inferred from recency:
snapshot the residents alive at suite start, reap anything that appeared
since. That is only equivalent to "ours" on a machine where nothing else is
running tests. With several agents measuring at once it silently killed
*their* residents -- exit code 1, no traceback, no crash dump, because
``taskkill /F`` gives a process no chance to say anything. It cost a
teammate most of an investigation, and the symptom looked exactly like an
engine crash. Recency is not ownership, and no amount of settle time makes
it so.

**Positive identification instead.** The suite claims a private embed port
before importing any mnemo module and exports it as ``MNEMO_EMBED_PORT``.
Every process the suite spawns inherits that variable, and so does anything
*they* spawn -- a backend started by the suite passes it to the resident it
autostarts. So "ours" becomes a checkable fact about the machine: the
process listening on our private port, plus any PID the suite spawned itself
and registered with ``track()``.

Nothing else is ever touched, however new it is.
"""
from __future__ import annotations

import os
import socket
import subprocess
import time


def claim_embed_port() -> int:
    """Reserve a private embed port and export it. Call BEFORE importing src.

    ``config`` reads ``MNEMO_EMBED_PORT`` at import time, so the order
    matters: set it first and the whole suite -- CLI, backend, resident --
    agrees on a port no other agent is using.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    os.environ["MNEMO_EMBED_PORT"] = str(port)
    return port


def listening_pid(port: int) -> int | None:
    """PID owning the LISTEN socket on ``port``, straight from the OS."""
    if os.name == "nt":
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, errors="replace", timeout=120,
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                if parts[1].rsplit(":", 1)[-1] == str(port) and parts[4].isdigit():
                    return int(parts[4])
        return None
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True, text=True, errors="replace", timeout=120,
    )
    for value in result.stdout.split():
        if value.strip().isdigit():
            return int(value)
    return None


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=60, check=False)
        return
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, errors="replace", timeout=60,
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class ResidentGuard:
    """Reap only what this suite can be shown to have caused."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.tracked: set[int] = set()

    def track(self, pid: int) -> None:
        """Register a PID the suite spawned itself."""
        self.tracked.add(pid)

    def reap(self, *, settle: float = 3.0) -> None:
        """Terminate the resident on our private port, plus tracked PIDs.

        ``settle`` because a resident spawned moments earlier may still be
        finishing its bind; sweeping instantly once reported success over a
        real leak.
        """
        time.sleep(settle)

        targets: list[int] = []
        owner = listening_pid(self.port)
        if owner is not None:
            targets.append(owner)
        targets.extend(pid for pid in sorted(self.tracked) if _alive(pid))

        unique = list(dict.fromkeys(targets))
        for pid in unique:
            _kill_tree(pid)

        # Always report, including the zero case: a silent teardown has meant
        # "clean" and "the probe failed" indistinguishably once already.
        leftover = listening_pid(self.port)
        print(
            f"teardown: private embed port {self.port}, reaped={len(unique)}"
            + (f", STILL BOUND by {leftover}" if leftover else "")
        )
