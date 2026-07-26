"""Test hygiene: never leave an embedding resident behind.

``MNEMO_EMBED_IDLE_TIMEOUT`` now defaults to 0, so a resident started during
a test never exits by itself. Two things follow, and both have bitten:

* A suite that spawns one and walks away strands ~1.5 GB until reboot.
* ``service_ctl.stop_resident()`` only ever inspects the *configured* embed
  port, so a resident that ended up on any other port is invisible to it --
  no command on the machine can find it again.

So teardown here is by process identity, not by port: snapshot the residents
alive before the suite, reap whatever appeared since. That reaps ours
whatever port it chose, and never touches one the user already had running.
"""
from __future__ import annotations

import os
import subprocess
import time


def _resident_pids() -> set[int]:
    """PIDs of every embedding resident alive right now.

    Raises rather than returning an empty set when the probe itself fails: a
    hygiene helper that silently reports "nothing to clean up" whenever its
    query times out is indistinguishable from a clean machine, and would let
    exactly the leak it exists to prevent go unnoticed.
    """
    if os.name == "nt":
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
            "Where-Object { $_.CommandLine -like '*embed-server*' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        argv = ["powershell", "-NoProfile", "-Command", script]
    else:
        argv = ["pgrep", "-f", "src.cli embed-server"]
    result = subprocess.run(
        argv, capture_output=True, text=True, errors="replace", timeout=120,
    )
    # pgrep exits 1 for "no matches", which is a legitimate empty answer;
    # PowerShell exits 0 with empty output for the same case.
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"resident probe failed ({result.returncode}): {result.stderr.strip()[:200]}"
        )
    return {int(value) for value in result.stdout.split() if value.strip().isdigit()}


class ResidentGuard:
    """Snapshot residents on entry; reap the new ones on exit."""

    def __init__(self) -> None:
        self.before: set[int] = set()

    def snapshot(self) -> None:
        self.before = _resident_pids()

    def reap(self, *, settle: float = 3.0) -> None:
        """Reap residents that appeared since the snapshot.

        ``settle`` matters: the backend is stopped moments earlier and a
        resident it spawned can still be finishing its bind, so an immediate
        sweep can miss it and report a clean run over a leaked 1.5 GB.
        """
        time.sleep(settle)
        now = _resident_pids()
        strays = now - self.before
        for pid in sorted(strays):
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=60, check=False,
                )
            else:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
        # Always report, including the zero case: silence here has meant
        # "clean" and "the probe failed" indistinguishably once already.
        print(
            f"teardown: residents before={len(self.before)} after={len(now)} "
            f"reaped={len(strays)}"
        )
        left = _resident_pids() - self.before
        if left:
            print(f"teardown: WARNING {len(left)} resident(s) survived: {sorted(left)}")
