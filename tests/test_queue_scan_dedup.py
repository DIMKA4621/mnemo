"""MN-31: the watcher's periodic rescan must not pile up per bank, must not
wait behind another bank's first build, and a waiting no-op check must not
paint a bank as indexing.

Pure module state, no worker threads and no embedding provider, same as
test_pending_paths.py.

    .venv/bin/python tests/test_queue_scan_dedup.py
"""

import itertools
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_STATE = tempfile.mkdtemp(prefix="mnemo scan-dedup state ")
os.environ["MNEMO_STATE_DIR"] = _STATE

from src import api, registry, workqueue  # noqa: E402
from src.workqueue import Priority  # noqa: E402

_passed = 0
_failed = 0
_ids = itertools.count()


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")
    assert ok, f"{name}  {detail}"


def _reset_queue() -> None:
    workqueue.clear()
    workqueue._running.clear()  # noqa: SLF001 - test-only reach into internals
    workqueue._scanning.clear()  # noqa: SLF001
    workqueue._cancelled.clear()  # noqa: SLF001
    workqueue._parked.clear()  # noqa: SLF001
    workqueue._consecutive_high = 0  # noqa: SLF001


def _bank_id() -> str:
    return f"bank{next(_ids)}"


def _waiting_scans(bank_id: str) -> list[workqueue.Task]:
    return [t for t in workqueue._waiting.values()  # noqa: SLF001
            if t.bank_id == bank_id and t.kind in ("bulk", "rebuild")]


def _take_order() -> list[workqueue.Task]:
    order = []
    with workqueue._cv:  # noqa: SLF001
        while (task := workqueue._select()) is not None:  # noqa: SLF001
            order.append(task)
    return order


def test_repeated_bulk_is_one_task() -> None:
    _reset_queue()
    bank = _bank_id()
    first = workqueue.enqueue_bulk(bank, trigger="watcher")
    again = [workqueue.enqueue_bulk(bank, trigger="watcher") for _ in range(3)]
    check("repeated bulk: one waiting task", len(_waiting_scans(bank)) == 1,
          detail=str(_waiting_scans(bank)))
    check("repeated bulk: the existing id is returned",
          all(task_id == first for task_id in again), detail=str(again))


def test_explicit_trigger_survives_the_merge() -> None:
    _reset_queue()
    bank = _bank_id()
    workqueue.enqueue_bulk(bank, trigger="watcher")
    workqueue.enqueue_bulk(bank, trigger="api")
    workqueue.enqueue_bulk(bank, trigger="startup")
    check("merged bulk keeps the explicit trigger",
          [t.trigger for t in _waiting_scans(bank)] == ["api"],
          detail=str(_waiting_scans(bank)))


def test_rebuild_absorbs_bulk() -> None:
    _reset_queue()
    bank = _bank_id()
    bulk_id = workqueue.enqueue_bulk(bank, trigger="watcher")
    rebuild_id = workqueue.enqueue_bulk(bank, trigger="api", rebuild=True)
    scans = _waiting_scans(bank)
    check("rebuild absorbs a waiting bulk",
          [t.kind for t in scans] == ["rebuild"] and rebuild_id != bulk_id,
          detail=str(scans))
    check("bulk after a waiting rebuild returns the rebuild",
          workqueue.enqueue_bulk(bank, trigger="watcher") == rebuild_id)
    check("second rebuild returns the first",
          workqueue.enqueue_bulk(bank, trigger="cli", rebuild=True) == rebuild_id)
    check("still one waiting scan", len(_waiting_scans(bank)) == 1,
          detail=str(_waiting_scans(bank)))
    check("absorbed bulk is never selected",
          [t.id for t in _take_order()] == [rebuild_id])


def test_scans_are_per_bank() -> None:
    _reset_queue()
    a, b = _bank_id(), _bank_id()
    workqueue.enqueue_bulk(a, trigger="watcher")
    workqueue.enqueue_bulk(b, trigger="watcher")
    check("each bank keeps its own scan",
          len(_waiting_scans(a)) == 1 and len(_waiting_scans(b)) == 1)


def test_cancelled_bank_gets_no_scan() -> None:
    _reset_queue()
    bank = _bank_id()
    workqueue._cancelled.add(bank)  # noqa: SLF001
    workqueue.enqueue_bulk(bank, trigger="watcher")
    check("cancelled bank: bulk refused", _waiting_scans(bank) == [])


def test_bulk_dispatched_before_queued_low_files() -> None:
    _reset_queue()
    heavy, idle = _bank_id(), _bank_id()
    for n in range(5):
        workqueue.enqueue_file(heavy, f"f{n}.md", priority=Priority.LOW,
                               trigger="api")
    bulk_id = workqueue.enqueue_bulk(idle, trigger="watcher")
    edit_id = workqueue.enqueue_file(heavy, "edit.md", priority=Priority.HIGH,
                                     trigger="watcher")
    order = [t.id for t in _take_order()]
    check("HIGH edit first, then the bulk, then LOW files",
          order[:2] == [edit_id, bulk_id] and len(order) == 7, detail=str(order))


def test_lone_waiting_bulk_is_not_depth() -> None:
    _reset_queue()
    quiet, heavy = _bank_id(), _bank_id()
    workqueue.enqueue_bulk(quiet, trigger="watcher")
    workqueue.enqueue_file(heavy, "a.md", priority=Priority.LOW, trigger="api")
    snap = workqueue.snapshot()
    check("by_bank: no entry for a bank with only a waiting bulk",
          quiet not in snap.by_bank, detail=str(snap.by_bank))
    check("depth/_queued ignore the lone bulk",
          workqueue.depth(quiet) == 0 and api._queued(quiet) == 0)  # noqa: SLF001
    check("scan_pending sees it", workqueue.scan_pending(quiet))
    check("totals agree with by_bank",
          snap.depth == 1 and snap.low == 1 and snap.normal == 0
          and sum(e["depth"] for e in snap.by_bank.values()) == snap.depth,
          detail=str(snap))

    workqueue.enqueue_file(quiet, "b.md", priority=Priority.LOW, trigger="api")
    check("a waiting file beside the bulk counts once",
          workqueue.snapshot().by_bank[quiet]["depth"] == 1
          and api._queued(quiet) == 1)  # noqa: SLF001


def test_waiting_rebuild_counts() -> None:
    _reset_queue()
    bank = _bank_id()
    workqueue.enqueue_bulk(bank, trigger="api", rebuild=True)
    check("waiting rebuild is depth", workqueue.depth(bank) == 1
          and workqueue.snapshot().by_bank[bank]["depth"] == 1)


def test_running_scan_counts_as_busy() -> None:
    _reset_queue()
    bank = _bank_id()
    workqueue._scanning.add(bank)  # noqa: SLF001
    try:
        entry = workqueue.snapshot().by_bank.get(bank)
        check("by_bank: running scan is indexing",
              entry == {"depth": 0, "indexing": True}, detail=str(entry))
        check("api._busy: running scan", api._busy(bank))  # noqa: SLF001
    finally:
        workqueue._scanning.clear()  # noqa: SLF001

    task = workqueue._task(bank, "bulk", Priority.NORMAL, "watcher")  # noqa: SLF001
    workqueue._running["test-worker"] = workqueue._Running(  # noqa: SLF001
        task=task, started_at=time.time(),
    )
    try:
        check("by_bank: running bulk dispatch is indexing",
              workqueue.snapshot().by_bank[bank]["indexing"] is True)
    finally:
        workqueue._running.clear()  # noqa: SLF001


def test_status_from_lone_bulk() -> None:
    status = api._status_from  # noqa: SLF001
    check("indexed bank + lone waiting bulk -> ready",
          status((0, False, True), 120) == ("ready", 0))
    check("empty bank + lone waiting bulk -> indexing (first build pending)",
          status((0, False, True), 0) == ("indexing", 0))
    check("empty bank, nothing waiting -> empty",
          status((0, False, False), 0) == ("empty", 0))
    check("running scan -> indexing", status((0, True, False), 120)[0] == "indexing")


def test_rebuild_waits_for_in_flight_scan() -> None:
    _reset_queue()
    root = Path(tempfile.mkdtemp(prefix="mnemo scan-dedup bank "))
    (root / "a.md").write_text("# A")
    bank = registry.add(root, name=f"scan-dedup-{next(_ids)}")
    provider = MagicMock(key="fake", dim=4)
    wipes: list[str] = []
    scans: list[str] = []

    def fake_wipe(b: registry.Bank):
        wipes.append(b.id)
        return MagicMock(), provider

    def fake_scan(task: workqueue.Task, b: registry.Bank) -> None:
        scans.append(task.kind)

    def run(kind: str, trigger: str) -> None:
        workqueue._run_bulk(  # noqa: SLF001
            workqueue._task(bank.id, kind, Priority.NORMAL, trigger),  # noqa: SLF001
            bank, rebuild=kind == "rebuild",
        )

    with (
        patch.object(workqueue, "get_provider", return_value=provider),
        patch.object(workqueue.store, "connect", return_value=MagicMock()),
        patch.object(workqueue.store, "needs_rebuild", return_value=False),
        patch.object(workqueue, "_open_for_rebuild", side_effect=fake_wipe),
        patch.object(workqueue, "_scan_bank_streaming", side_effect=fake_scan),
    ):
        workqueue._scanning.add(bank.id)  # noqa: SLF001
        run("rebuild", "watcher")
        run("rebuild", "api")
        run("bulk", "watcher")
        parked = workqueue._parked.get(bank.id)  # noqa: SLF001
        check("rebuild under a live scan: no wipe", wipes == [], detail=str(wipes))
        check("rebuild under a live scan: one parked, explicit trigger kept",
              parked is not None and parked.kind == "rebuild"
              and parked.trigger == "api", detail=str(parked))
        check("parked rebuild is not selectable: no worker spin",
              _take_order() == [])
        check("bank still reads busy while the scan runs", api._busy(bank.id))  # noqa: SLF001

        workqueue.enqueue_bulk(bank.id, trigger="watcher")
        workqueue._release_scan(bank.id)  # noqa: SLF001
        scans_waiting = _waiting_scans(bank.id)
        check("scan end re-queues the rebuild, absorbing a waiting bulk",
              [(t.kind, t.trigger) for t in scans_waiting] == [("rebuild", "api")]
              and bank.id not in workqueue._parked  # noqa: SLF001
              and bank.id not in workqueue._scanning,  # noqa: SLF001
              detail=str(scans_waiting))

        order = _take_order()
        workqueue._run_bulk(order[0], bank, rebuild=True)  # noqa: SLF001
        for thread in list(workqueue._scanners):  # noqa: SLF001
            thread.join(timeout=5)
        check("re-queued rebuild wipes once and scans",
              wipes == [bank.id] and scans == ["rebuild"],
              detail=f"{wipes} {scans}")
    workqueue._scanning.clear()  # noqa: SLF001
    workqueue._scanners.clear()  # noqa: SLF001


if __name__ == "__main__":
    for _test in (
        test_repeated_bulk_is_one_task,
        test_explicit_trigger_survives_the_merge,
        test_rebuild_absorbs_bulk,
        test_scans_are_per_bank,
        test_cancelled_bank_gets_no_scan,
        test_bulk_dispatched_before_queued_low_files,
        test_lone_waiting_bulk_is_not_depth,
        test_waiting_rebuild_counts,
        test_running_scan_counts_as_busy,
        test_status_from_lone_bulk,
        test_rebuild_waits_for_in_flight_scan,
    ):
        try:
            _test()
        except AssertionError:
            pass
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
