"""MN-15: paths that are queued or in flight must be reportable per bank,
so the console can highlight a file the instant the watcher picks it up
instead of waiting for indexing to finish.

Pure module state, no worker threads and no embedding provider: `enqueue*`
only ever touches `workqueue`'s internal dicts, and `pending_paths` reads
them back. `api.api_tree` never touches sqlite-vec either when a bank has
no chunks yet, same as test_tree_prune.py.

    .venv/bin/python tests/test_pending_paths.py
"""
from __future__ import annotations

import itertools
import os
import sys
import tempfile
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `mkdtemp`, not `TemporaryDirectory` — same Windows/servicelog reasoning as
# test_tree_prune.py / test_watcher.py.
_STATE = tempfile.mkdtemp(prefix="mnemo pending-paths state ")
os.environ["MNEMO_STATE_DIR"] = _STATE

from src import api, registry, workqueue  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def _reset_queue() -> None:
    """Module-level queue state is shared across tests in this process."""
    workqueue.clear()
    workqueue._running.clear()  # noqa: SLF001 - test-only reach into internals
    workqueue._scanning.clear()  # noqa: SLF001
    workqueue._cancelled.clear()  # noqa: SLF001


_counter = itertools.count()


def _make_bank(prefix: str) -> registry.Bank:
    root = Path(tempfile.mkdtemp(prefix=f"mnemo {prefix} bank "))
    (root / "a.md").write_text("# A")
    return registry.add(root, name=f"pending-paths-{prefix}-{next(_counter)}")


# -------------------------------------------------------- workqueue.pending_paths


def test_idle_bank_has_no_pending_paths() -> None:
    _reset_queue()
    bank = _make_bank("idle")
    check("idle bank: empty pending set",
          workqueue.pending_paths(bank.id) == set())


def test_queued_file_task_shows_up() -> None:
    _reset_queue()
    bank = _make_bank("queued")
    workqueue.enqueue_file(bank.id, "a.md", trigger="watcher")
    check("queued file task: path is pending",
          workqueue.pending_paths(bank.id) == {"a.md"},
          detail=str(workqueue.pending_paths(bank.id)))


def test_current_file_task_still_shows_up() -> None:
    """A task moved from `_waiting` into `_running` (the worker took it) is
    still "in flight" — the ticket's highlight covers both states with the
    same color, so `pending_paths` must not drop it the moment work starts.
    """
    _reset_queue()
    bank = _make_bank("current")
    task = workqueue._task(  # noqa: SLF001
        bank.id, "file", workqueue.Priority.HIGH, "watcher", path="a.md",
    )
    workqueue._running["test-worker"] = workqueue._Running(  # noqa: SLF001
        task=task, started_at=time.time(),
    )
    try:
        check("running file task: path is pending",
              workqueue.pending_paths(bank.id) == {"a.md"},
              detail=str(workqueue.pending_paths(bank.id)))
    finally:
        workqueue._running.clear()  # noqa: SLF001


def test_bulk_task_contributes_nothing() -> None:
    """A `bulk`/`rebuild` task has no single path — it must never show up in
    a per-file pending set, queued or running."""
    _reset_queue()
    bank = _make_bank("bulk")
    workqueue.enqueue_bulk(bank.id, trigger="watcher")
    check("queued bulk task: no pending paths",
          workqueue.pending_paths(bank.id) == set(),
          detail=str(workqueue.pending_paths(bank.id)))

    task = workqueue._task(  # noqa: SLF001
        bank.id, "bulk", workqueue.Priority.LOW, "watcher",
    )
    workqueue._running["test-worker"] = workqueue._Running(  # noqa: SLF001
        task=task, started_at=time.time(),
    )
    try:
        check("running bulk task: still no pending paths",
              workqueue.pending_paths(bank.id) == set(),
              detail=str(workqueue.pending_paths(bank.id)))
    finally:
        workqueue._running.clear()  # noqa: SLF001


def test_prune_task_shows_up_too() -> None:
    _reset_queue()
    bank = _make_bank("prune")
    workqueue.enqueue_prune(bank.id, "gone.md", trigger="watcher")
    check("queued prune task: path is pending",
          workqueue.pending_paths(bank.id) == {"gone.md"},
          detail=str(workqueue.pending_paths(bank.id)))


def test_other_bank_is_not_polluted() -> None:
    _reset_queue()
    bank_a = _make_bank("a")
    bank_b = _make_bank("b")
    workqueue.enqueue_file(bank_a.id, "a.md", trigger="watcher")
    check("bank B sees none of bank A's pending paths",
          workqueue.pending_paths(bank_b.id) == set())
    check("bank A sees its own pending path",
          workqueue.pending_paths(bank_a.id) == {"a.md"})


# ------------------------------------------------------------ through api_tree


def test_api_tree_carries_pending_field() -> None:
    _reset_queue()
    bank = _make_bank("tree")

    idle = api.api_tree(bank.name)
    check("api_tree: idle bank reports pending=[]", idle["pending"] == [],
          detail=str(idle.get("pending")))

    workqueue.enqueue_file(bank.id, "a.md", trigger="watcher")
    busy = api.api_tree(bank.name)
    check("api_tree: queued file shows up in pending",
          busy["pending"] == ["a.md"], detail=str(busy.get("pending")))


if __name__ == "__main__":
    test_idle_bank_has_no_pending_paths()
    test_queued_file_task_shows_up()
    test_current_file_task_still_shows_up()
    test_bulk_task_contributes_nothing()
    test_prune_task_shows_up_too()
    test_other_bank_is_not_polluted()
    test_api_tree_carries_pending_field()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
