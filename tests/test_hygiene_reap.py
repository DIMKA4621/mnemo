"""Teardown must reap by ownership, never by recency.

`_hygiene` used to infer ownership from recency: snapshot the residents alive
at suite start, kill anything newer. On a one-agent machine that is
indistinguishable from ownership. With several suites running at once it
killed *other agents'* residents — exit 1, no traceback, no faulthandler
dump, because `taskkill /F` gives a process no chance to speak. The symptom
was a perfect forgery of an engine crash and it cost most of an
investigation. platform-dev has since replaced it with positive
identification; this file is the guard that keeps it replaced.

**Writing this test taught the same lesson it encodes.** The obvious
assertion — "a foreign resident is not killed" — is now true *by
construction*, because `reap()` no longer enumerates anything. It would pass
forever without testing a thing, which is the exact hollow-green shape this
project spent a day removing. The assertion that can actually fail is one
level down: **teardown must never enumerate the machine's processes at all.**
Recency-based reaping is impossible without a sweep, so banning the sweep
bans the bug, and any future reintroduction trips this immediately.

Nothing here starts or kills a real process.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))

import _hygiene  # noqa: E402
from src import config  # noqa: E402

_passed = _failed = 0

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Commands that answer "what is running on this machine?". Teardown has no
# business asking: the question only has a use if you intend to judge
# ownership by what the answer contains.
_ENUMERATORS = ("pgrep", "tasklist", "ps")
_ENUMERATING_PS = ("win32_process", "get-process")


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


class Recorder:
    """Stand in for every OS call `reap()` can make, and record instead."""

    def __init__(self, port_owner: int | None, alive: set[int]) -> None:
        self.port_owner = port_owner
        self.alive = alive
        self.killed: list[int] = []
        self.ports_queried: list[int] = []
        self.enumerated: list[str] = []

    def listening_pid(self, port: int) -> int | None:
        self.ports_queried.append(port)
        return self.port_owner if port == self.port else None

    def kill_tree(self, pid: int) -> None:
        self.killed.append(pid)

    def alive_probe(self, pid: int) -> bool:
        return pid in self.alive

    def run(self, argv, **kwargs):
        """Any surviving real subprocess call lands here and is classified."""
        joined = " ".join(str(a) for a in argv).lower()
        if any(joined.startswith(name) for name in _ENUMERATORS) or any(
            token in joined for token in _ENUMERATING_PS
        ):
            self.enumerated.append(joined[:120])

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()


def run_reap(port: int, port_owner: int | None, tracked: set[int],
             alive: set[int]) -> Recorder:
    rec = Recorder(port_owner, alive)
    rec.port = port
    saved = (_hygiene.listening_pid, _hygiene._kill_tree,
             _hygiene._alive, _hygiene.subprocess.run)
    _hygiene.listening_pid = rec.listening_pid
    _hygiene._kill_tree = rec.kill_tree
    _hygiene._alive = rec.alive_probe
    _hygiene.subprocess.run = rec.run
    try:
        guard = _hygiene.ResidentGuard(port)
        for pid in tracked:
            guard.track(pid)
        guard.reap(settle=0.0)
    finally:
        (_hygiene.listening_pid, _hygiene._kill_tree,
         _hygiene._alive, _hygiene.subprocess.run) = saved
    return rec


def test_reap_targets_only_what_it_owns() -> None:
    port, owner, tracked = 54321, 250, 300
    rec = run_reap(port, owner, {tracked}, alive={owner, tracked, 400})

    check("the resident on our private port is reaped",
          owner in rec.killed, detail=str(rec.killed))
    check("a PID the suite spawned and tracked is reaped",
          tracked in rec.killed, detail=str(rec.killed))
    check("nothing beyond the port owner and tracked PIDs is reaped",
          set(rec.killed) <= {owner, tracked}, detail=str(rec.killed))

    # The load-bearing one. Recency-based reaping cannot be implemented
    # without asking the machine what is running, so a clean sheet here is
    # what makes "ownership, not recency" a structural property rather than
    # a promise in a docstring.
    check("teardown never enumerates the machine's processes",
          not rec.enumerated, detail=str(rec.enumerated))

    # Ownership must be decided about OUR port and no other.
    check("teardown asks about its own port only",
          set(rec.ports_queried) <= {port}, detail=str(rec.ports_queried))


def test_dead_tracked_pid_is_not_killed() -> None:
    """A tracked PID that already exited must not be signalled again — the
    number may belong to somebody else by now."""
    port, owner, tracked = 54321, 250, 300
    rec = run_reap(port, owner, {tracked}, alive={owner})  # tracked has exited
    check("an exited tracked PID is not signalled (PIDs get reused)",
          tracked not in rec.killed, detail=str(rec.killed))


def test_claim_embed_port_is_private_and_exported() -> None:
    saved = os.environ.get("MNEMO_EMBED_PORT")
    try:
        port = _hygiene.claim_embed_port()
        check("claim_embed_port exports MNEMO_EMBED_PORT",
              os.environ.get("MNEMO_EMBED_PORT") == str(port),
              detail=str(os.environ.get("MNEMO_EMBED_PORT")))
        # config.EMBED_PORT is the shared default; a suite landing on it
        # would fight the user's own resident, which is the collision this
        # whole file exists to prevent. Read from config, not a hardcoded
        # literal, so a future renumber can't silently make this pass while
        # testing nothing.
        check("the claimed port is not the shared default",
              port != config.EMBED_PORT, detail=str(port))
        check("a second claim yields a different port",
              _hygiene.claim_embed_port() != port)
    finally:
        if saved is None:
            os.environ.pop("MNEMO_EMBED_PORT", None)
        else:
            os.environ["MNEMO_EMBED_PORT"] = saved


def main() -> int:
    test_reap_targets_only_what_it_owns()
    test_dead_tracked_pid_is_not_killed()
    test_claim_embed_port_is_private_and_exported()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
