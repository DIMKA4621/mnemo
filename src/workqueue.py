"""The work queue: one writer, many producers, and an urgent edit that wins.

Everything that changes an index goes through here (Memory-contracts-v3 §8).
The watcher, the API, the CLI and startup reconcile all *enqueue*; a worker
thread is the only thing that opens a bank for writing. That is what keeps
"the backend is the sole writer" true without a lock protocol.

**Why a priority queue and not a list.** A full rebuild of a large bank is
minutes of embedding. If the file you just saved sat behind it, the promise
that memory is searchable "within seconds of an edit" would be a lie whenever
it matters most. Three mechanisms together bound the wait (§8.3):

1. **Priority** — a `HIGH` task jumps every `LOW` one already waiting.
2. **Decomposition** — a `bulk` task never embeds, and never even scans on
   the worker: it hands the walk to its own thread, which streams one `file`
   task per changed file as it finds them. The longest thing that can block
   the queue is therefore one file, not one bank and not one scan.
3. **Preemption between batches** — a `LOW` file yields after any committed
   batch if a `HIGH` task is waiting, and is re-queued to resume where it
   stopped. Worst-case wait for an urgent edit is one batch (~16 chunks).

Priority is one-sided on its own, so a fourth rule bounds the other
direction: after `MNEMO_QUEUE_AGING` consecutive `HIGH` tasks the worker
takes one `LOW` regardless, and a steady stream of edits cannot starve a
bulk build forever.

`MNEMO_QUEUE_PRIORITY=0` turns priority, preemption and aging off:
everything becomes `NORMAL` and the queue is plain FIFO.

The queue deliberately imports neither `api` nor `servicelog`. It reports
through one `on_event` callback carrying ready-made §9.7 envelopes; whoever
starts the queue decides whether those go to a WebSocket, the journal, or
both. That is what keeps this module testable without a running service.
"""
from __future__ import annotations

import hashlib
import heapq
import itertools
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Literal

from . import config, index, registry, store
from .config import BATCH_SIZE
from .providers import EmbeddingUnavailable, get_provider

log = logging.getLogger("mnemo.queue")


class Priority(IntEnum):
    HIGH = 0     # a single edit — jumps the queue and preempts bulk work
    NORMAL = 1   # an explicit per-file reindex from the UI or CLI
    LOW = 2      # bulk: a new bank, a full rebuild, startup reconcile


TaskKind = Literal["file", "bulk", "prune", "rebuild"]

# index_progress is capped at 5/s per task: past that it is noise the UI
# cannot render anyway, and each event costs a broadcast to every client.
PROGRESS_INTERVAL_S = 0.2


@dataclass(frozen=True)
class Task:
    """One unit of indexing work."""

    id: str
    bank_id: str
    kind: TaskKind
    priority: Priority
    trigger: str             # 'watcher'|'api'|'startup'|'cli'|'mcp'|'ui'
    path: str | None = None  # relpath; required for kind='file' and 'prune'
    start_batch: int = 0     # >0 only for a resumed, preempted file
    enqueued_at: float = 0.0
    seq: int = 0             # monotonic; FIFO tiebreaker inside a priority


@dataclass
class QueueSnapshot:
    depth: int
    high: int
    normal: int
    low: int
    current: Task | None
    current_batch: int
    current_batches: int
    # Epoch seconds, absolute — never an elapsed count. Elapsed goes stale
    # the instant it is serialised, and a client seeding from a snapshot
    # would have to guess the snapshot's own age; from an epoch it computes
    # elapsed at render time. Unit matches `ts_epoch` in the journal rather
    # than inventing a second convention.
    #
    # Clock skew is not a concern: the backend is loopback-only, so the
    # client shares this clock. A remote backend would need this normalised.
    #
    # For a **resumed** task this is when the CURRENT run began, not the
    # first attempt — the worker builds a fresh `_Running` each time it takes
    # a task, and a file resumed at batch 7 has genuinely only been working
    # since the resume. On a large file the two readings differ by minutes.
    current_started_at: float = 0.0
    # bank_id -> {"depth": int, "indexing": bool}. Service-wide totals cannot
    # answer "is THIS bank busy", which is the question every bank row in the
    # cabinet asks and the question `status` is computed from.
    by_bank: dict[str, dict[str, Any]] = field(default_factory=dict)


# --------------------------------------------------------------- settings


def priority_enabled() -> bool:
    configured = getattr(config, "QUEUE_PRIORITY", None)
    if configured is not None:
        return bool(configured)
    return os.environ.get("MNEMO_QUEUE_PRIORITY", "1").strip() != "0"


def aging_threshold() -> int:
    """How many consecutive HIGH tasks before a LOW one is taken anyway.

    Priority alone is one-sided: a sustained stream of HIGH — an active
    editing session is exactly that — would keep a bulk build from ever
    finishing, which is the same starvation problem priorities were added to
    solve, pointed the other way. After N consecutive HIGH tasks the worker
    takes one LOW regardless, so both directions are bounded. 0 disables it.
    """
    configured = getattr(config, "QUEUE_AGING", None)
    if configured is not None:
        return int(configured)
    try:
        return max(0, int(os.environ.get("MNEMO_QUEUE_AGING", "8")))
    except ValueError:
        return 8


def default_workers() -> int:
    configured = getattr(config, "WORKERS", None)
    if configured is not None:
        return int(configured)
    try:
        return max(1, int(os.environ.get("MNEMO_WORKERS", "1")))
    except ValueError:
        return 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ state

_lock = threading.RLock()
_cv = threading.Condition(_lock)
# A bare heap plus a Condition, not `queue.PriorityQueue`. Three behaviours
# need to *choose* rather than merely pop the front: deduplication (drop a
# duplicate), promotion (re-prioritise something already queued) and aging
# (deliberately take a LOW while a HIGH waits). PriorityQueue exposes none of
# that, and emulating it with tombstones spread the scheduling decision over
# three places instead of one.
_heap: list[tuple[int, int, str]] = []          # (priority, seq, task_id)
# Waiting tasks by id — the authority on what is queued. A heap entry with no
# entry here is a tombstone left by a promotion, and is skipped.
_waiting: dict[str, Task] = {}
# (bank_id, path) -> task id, so a second event for the same file upgrades
# the pending task instead of queueing duplicate work.
_by_file: dict[tuple[str, str], str] = {}
_running: dict[str, "_Running"] = {}
# Banks whose scan thread is walking the tree right now. A scan is not queue
# depth and not a running task, but the bank IS busy — without this, status
# reads `empty`/`ready` while a scan is discovering 500 changed files, which
# is the single most visible moment to get wrong.
_scanning: set[str] = set()
# Banks being removed. `enqueue` refuses them and a running scan stops at its
# next file, so a removal is not racing a scanner that refills the queue.
_cancelled: set[str] = set()
_seq = itertools.count()
_consecutive_high = 0

_workers: list[threading.Thread] = []
_scanners: list[threading.Thread] = []
_stop = threading.Event()
_on_event: Callable[[dict], None] | None = None


@dataclass
class _Running:
    task: Task
    batch: int = 0
    batches: int = 0
    started_at: float = 0.0


# ------------------------------------------------------------------ events


def _emit(type_: str, bank_id: str | None, **data: Any) -> None:
    """Hand one §9.7 envelope to whoever is listening. Never raises."""
    callback = _on_event
    if callback is None:
        return
    try:
        callback(
            {
                "v": 1,
                "type": type_,
                "ts": _now_iso(),
                "bank_id": bank_id,
                "data": data,
            }
        )
    except Exception:  # noqa: BLE001 - a listener must not break indexing
        log.exception("on_event listener failed for %s", type_)


_queue_event_at = 0.0


def _emit_queue(force: bool = False) -> None:
    """Announce queue depth. Throttled like progress: a bulk that dissolves
    into 300 file tasks would otherwise broadcast 300 times in a second."""
    global _queue_event_at
    now = time.monotonic()
    if not force and now - _queue_event_at < PROGRESS_INTERVAL_S:
        return
    _queue_event_at = now
    snap = snapshot()
    current = snap.current
    _emit(
        "queue",
        None,
        depth=snap.depth,
        high=snap.high,
        normal=snap.normal,
        low=snap.low,
        current=None
        if current is None
        else {
            "task_id": current.id,
            "bank_id": current.bank_id,
            "kind": current.kind,
            "path": current.path,
            "batch": snap.current_batch,
            "batches": snap.current_batches,
            "started_at": snap.current_started_at,
        },
    )


# --------------------------------------------------------------- inspect


def depth(bank_id: str | None = None) -> int:
    """Tasks waiting. Running work is `busy`, not depth."""
    with _lock:
        if bank_id is None:
            return len(_waiting)
        return sum(1 for t in _waiting.values() if t.bank_id == bank_id)


def busy(bank_id: str | None = None) -> bool:
    """Is work for this bank in flight?

    Per bank, never "is the worker running": otherwise every bank reports
    `indexing` whenever any bank is building, and a per-bank status is the
    whole point of the field. A scan counts as busy — see `_scanning`.
    """
    with _lock:
        if bank_id is None:
            return bool(_running) or bool(_scanning)
        return (
            any(r.task.bank_id == bank_id for r in _running.values())
            or bank_id in _scanning
        )


def snapshot() -> QueueSnapshot:
    with _lock:
        counts = {Priority.HIGH: 0, Priority.NORMAL: 0, Priority.LOW: 0}
        by_bank: dict[str, dict[str, Any]] = {}
        for task in _waiting.values():
            counts[task.priority] = counts.get(task.priority, 0) + 1
            entry = by_bank.setdefault(
                task.bank_id, {"depth": 0, "indexing": False}
            )
            entry["depth"] += 1
        for running in _running.values():
            by_bank.setdefault(
                running.task.bank_id, {"depth": 0, "indexing": False}
            )["indexing"] = True
        for bank_id in _scanning:
            by_bank.setdefault(bank_id, {"depth": 0, "indexing": False})[
                "indexing"
            ] = True
        first = next(iter(_running.values()), None)
        return QueueSnapshot(
            depth=len(_waiting),
            high=counts[Priority.HIGH],
            normal=counts[Priority.NORMAL],
            low=counts[Priority.LOW],
            current=first.task if first else None,
            current_batch=first.batch if first else 0,
            current_batches=first.batches if first else 0,
            current_started_at=first.started_at if first else 0.0,
            by_bank=by_bank,
        )


def _high_waiting() -> bool:
    with _lock:
        return any(t.priority == Priority.HIGH for t in _waiting.values())


# --------------------------------------------------------------- enqueue


def _effective(priority: Priority) -> Priority:
    """With the flag off every task is NORMAL — one FIFO lane, no preemption."""
    return priority if priority_enabled() else Priority.NORMAL


def enqueue(task: Task) -> str:
    """Queue a task. A `file`/`prune` task for a path already waiting is
    deduplicated: same-or-lower priority is dropped, higher replaces it."""
    with _cv:
        if task.bank_id in _cancelled:
            return task.id      # bank is being removed; queueing is pointless
        priority = _effective(task.priority)
        key = (task.bank_id, task.path) if task.path else None
        start_batch = task.start_batch

        if key is not None:
            existing_id = _by_file.get(key)
            existing = _waiting.get(existing_id) if existing_id else None
            if existing is not None and existing.kind == task.kind:
                if priority >= existing.priority:
                    return existing.id          # already queued at least as urgently
                # Promotion must NOT reset start_batch: the queued task may be
                # a preempted file that already has N batches committed, and
                # discarding them would re-embed work that is already on disk.
                # This is only safe because `index_file` re-hashes the file
                # before resuming and falls back to start_batch=0 if it
                # changed — do not "simplify" one without the other.
                start_batch = start_batch or existing.start_batch
                del _waiting[existing.id]       # tombstone the old heap entry

        task = replace(
            task,
            priority=priority,
            start_batch=start_batch,
            seq=next(_seq),
            enqueued_at=time.time(),
            id=task.id or uuid.uuid4().hex[:12],
        )
        _waiting[task.id] = task
        if key is not None:
            _by_file[key] = task.id
        heapq.heappush(_heap, (int(priority), task.seq, task.id))
        _cv.notify()
    _emit_queue()
    return task.id


def _select() -> Task | None:
    """Choose the next task. Caller holds `_cv`.

    Normally the heap front. The exception is aging: after `aging_threshold()`
    consecutive HIGH tasks, take the oldest LOW instead, so a steady stream of
    edits cannot starve a bulk build forever.
    """
    global _consecutive_high
    while _heap and _heap[0][2] not in _waiting:
        heapq.heappop(_heap)                     # tombstone from a promotion
    if not _waiting:
        return None

    chosen_id: str | None = None
    threshold = aging_threshold()
    if (
        threshold
        and _consecutive_high >= threshold
        and _heap
        and _waiting[_heap[0][2]].priority == Priority.HIGH
    ):
        lows = [t for t in _waiting.values() if t.priority == Priority.LOW]
        if lows:
            chosen_id = min(lows, key=lambda t: t.seq).id
    if chosen_id is None:
        chosen_id = heapq.heappop(_heap)[2]

    task = _waiting.pop(chosen_id)
    key = (task.bank_id, task.path) if task.path else None
    if key is not None and _by_file.get(key) == task.id:
        del _by_file[key]
    if task.priority == Priority.HIGH:
        _consecutive_high += 1
    else:
        _consecutive_high = 0
    return task


def _take(timeout: float = 0.25) -> Task | None:
    with _cv:
        task = _select()
        if task is not None:
            return task
        _cv.wait(timeout)
        return _select()


def _task(bank_id: str, kind: TaskKind, priority: Priority, trigger: str,
          path: str | None = None, start_batch: int = 0) -> Task:
    return Task(
        id=uuid.uuid4().hex[:12],
        bank_id=bank_id,
        kind=kind,
        priority=priority,
        trigger=trigger,
        path=path,
        start_batch=start_batch,
    )


def enqueue_file(
    bank_id: str,
    path: str,
    *,
    priority: Priority = Priority.NORMAL,
    trigger: str = "api",
) -> str:
    return enqueue(_task(bank_id, "file", priority, trigger, path=path))


def enqueue_prune(
    bank_id: str,
    path: str,
    *,
    priority: Priority = Priority.LOW,
    trigger: str = "api",
) -> str:
    return enqueue(_task(bank_id, "prune", priority, trigger, path=path))


def enqueue_bulk(
    bank_id: str, *, trigger: str = "api", rebuild: bool = False
) -> str:
    kind: TaskKind = "rebuild" if rebuild else "bulk"
    return enqueue(_task(bank_id, kind, Priority.LOW, trigger))


# ------------------------------------------------------------- execution


def _open_bank(bank: registry.Bank):
    """Connect to a bank's index with its meta bound to the active provider.

    Returns ``(conn, provider, needs_rebuild)``. A provider or schema change
    is reported rather than acted on here: wiping an index is a `rebuild`
    task's job, not a side effect of the first file that happened to change.
    """
    provider = get_provider(bank.provider)
    conn = store.connect(bank.db_path)
    stale = store.needs_rebuild(conn, provider_key=provider.key, dim=provider.dim)
    # NOTE ordering: whoever wipes must wipe BEFORE the new provider_key is
    # committed. Writing meta first and dying in the window leaves meta
    # claiming provider B over provider A's vectors — `needs_rebuild` then
    # answers False forever and search silently blends incomparable
    # embeddings. Callers therefore `reset_index` first and call this after,
    # and this function itself only records.
    store.init_meta(
        conn,
        bank_id=bank.id,
        bank_root=bank.root.as_posix(),
        provider_key=provider.key,
        dim=provider.dim,
    )
    return conn, provider, stale


def _open_for_rebuild(bank: registry.Bank):
    """Open a bank that is about to be wiped: reset first, then record meta."""
    provider = get_provider(bank.provider)
    conn = store.connect(bank.db_path)
    store.reset_index(conn)
    store.init_meta(
        conn,
        bank_id=bank.id,
        bank_root=bank.root.as_posix(),
        provider_key=provider.key,
        dim=provider.dim,
    )
    return conn, provider


def _file_stat(bank: registry.Bank, rel: str) -> index.FileStat | None:
    """Hash one file into the shape the indexer wants, or None if it is gone."""
    abs_path = bank.root / rel
    try:
        stat = abs_path.stat()
        digest = file_sha256(abs_path)
    except OSError:
        return None
    return index.FileStat(
        path=rel,
        abs_path=abs_path,
        sha256=digest,
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def _run_file(task: Task, bank: registry.Bank, running: _Running) -> None:
    started = time.perf_counter()
    fs = _file_stat(bank, task.path or "")
    if fs is None:
        # Saved and deleted before we got to it: the path leaves the index.
        enqueue_prune(bank.id, task.path or "", priority=task.priority,
                      trigger=task.trigger)
        _emit("index_done", bank.id, task_id=task.id, kind="file",
              path=task.path, trigger=task.trigger, result="skipped",
              files_indexed=0, chunks_indexed=0, files_pruned=0,
              took_ms=(time.perf_counter() - started) * 1000)
        return

    conn, provider, stale = _open_bank(bank)
    try:
        if stale:
            # The stored vectors were made by a different model; adding one
            # more file to them would mix two vector spaces in one table.
            enqueue_bulk(bank.id, trigger=task.trigger, rebuild=True)
            _emit("index_done", bank.id, task_id=task.id, kind="file",
                  path=task.path, trigger=task.trigger, result="skipped",
                  files_indexed=0, chunks_indexed=0, files_pruned=0,
                  took_ms=(time.perf_counter() - started) * 1000)
            return

        last_batch = task.start_batch - 1
        throttle = 0.0
        yielded = False

        def on_batch(result: index.BatchResult) -> None:
            nonlocal last_batch, throttle
            last_batch = result.batch
            with _lock:
                running.batch = result.batch + 1
                running.batches = result.batches
            now = time.monotonic()
            final = result.batch + 1 >= result.batches
            if final or now - throttle >= PROGRESS_INTERVAL_S:
                throttle = now
                # Both counts come from the indexer, which holds the batch
                # plan. They used to be reconstructed here from BATCH_SIZE,
                # and that reconstruction is now impossible as well as
                # inexact: `plan_batches` sizes each batch by padded cost, so
                # batches within one file differ in length. The old estimate
                # was an upper bound that a 2-chunk file showed as "2/16";
                # this is the real figure from the first batch onward.
                _emit("index_progress", bank.id, task_id=task.id,
                      path=result.path, batch=result.batch + 1,
                      batches=result.batches,
                      chunks_done=result.chunks_done,
                      chunks_total=result.chunks_total)

        def should_yield() -> bool:
            nonlocal yielded
            if not priority_enabled() or task.priority != Priority.LOW:
                return False
            if not _high_waiting():
                return False
            yielded = True
            return True

        _emit("index_start", bank.id, task_id=task.id, kind="file",
              path=task.path, batches=0, trigger=task.trigger)
        written = index.index_file(
            conn, provider, fs,
            batch_size=BATCH_SIZE,
            start_batch=task.start_batch,
            on_batch=on_batch,
            should_yield=should_yield,
        )
        took_ms = (time.perf_counter() - started) * 1000

        if written is None and yielded:
            resume = last_batch + 1
            enqueue(_task(bank.id, "file", task.priority, task.trigger,
                          path=task.path, start_batch=resume))
            _emit("index_yield", bank.id, task_id=task.id, path=task.path,
                  resume_batch=resume)
            return
        if written is None:
            _emit("index_done", bank.id, task_id=task.id, kind="file",
                  path=task.path, trigger=task.trigger, result="skipped",
                  files_indexed=0, chunks_indexed=0, files_pruned=0,
                  took_ms=took_ms)
            return

        store.mark_indexed(conn)
        conn.commit()
        _emit("index_done", bank.id, task_id=task.id, kind="file",
              path=task.path, trigger=task.trigger, result="ok",
              files_indexed=1, chunks_indexed=written, files_pruned=0,
              took_ms=took_ms)
    finally:
        conn.close()


def _run_bulk(task: Task, bank: registry.Bank, *, rebuild: bool) -> None:
    """Hand the scan to its own thread and free the worker immediately.

    The scan sha256s every ``.md`` in the bank — seconds on hundreds of files,
    tens of seconds on thousands, with no yield point. Doing that on the
    worker would make "the longest thing blocking the queue is one file"
    false for exactly as long as the scan runs, and it runs precisely when a
    bank was just registered and the user is most likely still editing.

    It is safe off-worker because scanning is a pure read: no provider, no
    write, nothing that has to serialise with the single writer. The scanner
    streams ``enqueue_file`` as it finds changes, so the worker can start on
    the first changed file long before the walk finishes.
    """
    # Stale = a different model produced the stored vectors. Escalate here
    # rather than let each spawned file task discover it separately.
    conn = store.connect(bank.db_path, ensure=False)
    try:
        provider = get_provider(bank.provider)
        stale = store.needs_rebuild(
            conn, provider_key=provider.key, dim=provider.dim
        )
    finally:
        conn.close()
    if rebuild or stale:
        conn, _provider = _open_for_rebuild(bank)   # wipe, THEN record meta
        conn.close()

    with _lock:
        if bank.id in _scanning:
            # A scan is already walking this bank; a second one would only
            # enqueue the same paths again.
            _emit("index_done", bank.id, task_id=task.id, kind=task.kind,
                  path=None, trigger=task.trigger, result="skipped",
                  files_indexed=0, chunks_indexed=0, files_pruned=0,
                  took_ms=0.0)
            return
        _scanning.add(bank.id)

    thread = threading.Thread(
        target=_scan_bank_streaming,
        args=(task, bank),
        name=f"mnemo-scan-{bank.name}",
        daemon=True,
    )
    with _lock:
        _scanners.append(thread)
    thread.start()


def _scan_bank_streaming(task: Task, bank: registry.Bank) -> None:
    """Walk the bank, enqueueing each changed file the moment it is found."""
    started = time.perf_counter()
    planned = pruned = 0
    try:
        conn = store.connect(bank.db_path, ensure=False)
        try:
            indexed = store.get_indexed_hashes(conn)
        finally:
            conn.close()

        seen: set[str] = set()
        for abs_path in sorted(bank.root.rglob("*.md")):
            if _stop.is_set() or bank.id in _cancelled:
                return
            if not abs_path.is_file():
                continue
            rel = abs_path.relative_to(bank.root).as_posix()
            if index.path_is_excluded(rel, bank.exclude):
                continue
            seen.add(rel)
            try:
                digest = file_sha256(abs_path)
            except OSError:
                continue          # unreadable now; the next pass decides
            if indexed.get(rel) == digest:
                continue
            enqueue_file(bank.id, rel, priority=Priority.LOW,
                         trigger=task.trigger)
            planned += 1

        for rel in sorted(set(indexed) - seen):
            if bank.id in _cancelled:
                return
            enqueue_prune(bank.id, rel, priority=task.priority,
                          trigger=task.trigger)
            pruned += 1
    except Exception as exc:  # noqa: BLE001
        log.exception("scan of bank %s failed", bank.name)
        _emit("index_error", bank.id, task_id=task.id, kind=task.kind,
              path=None, trigger=task.trigger,
              error=f"{type(exc).__name__}: {exc}")
        return
    finally:
        with _lock:
            _scanning.discard(bank.id)
        _emit_queue(force=True)

    _emit("index_done", bank.id, task_id=task.id, kind=task.kind,
          path=None, trigger=task.trigger, result="ok",
          # A bulk indexes nothing itself; the counters belong to the file
          # tasks it spawned, which report their own rows.
          files_indexed=0, chunks_indexed=0, files_pruned=0,
          planned_files=planned, planned_prunes=pruned,
          took_ms=(time.perf_counter() - started) * 1000)


def _run_prune(task: Task, bank: registry.Bank) -> None:
    started = time.perf_counter()
    rel = task.path or ""
    conn = store.connect(bank.db_path)
    try:
        # Only count a path that was actually indexed: a delete event for a
        # file mnemo never saw must not report phantom prunes.
        known = store.get_file_row(conn, rel) is not None
        removed = index.prune(conn, [rel]) if known else 0
    finally:
        conn.close()
    if removed:
        _emit("prune", bank.id, paths=[rel], count=removed)
    _emit("index_done", bank.id, task_id=task.id, kind="prune",
          path=rel, trigger=task.trigger,
          result="ok" if removed else "skipped",
          files_indexed=0, chunks_indexed=0, files_pruned=removed,
          took_ms=(time.perf_counter() - started) * 1000)


def _execute(task: Task, running: _Running) -> None:
    try:
        bank = registry.get(task.bank_id)
    except registry.BankNotFound:
        # Unregistered mid-flight. Not an error worth alarming about: the
        # work simply has no target any more.
        _emit("index_done", task.bank_id, task_id=task.id, kind=task.kind,
              path=task.path, trigger=task.trigger, result="skipped",
              files_indexed=0, chunks_indexed=0, files_pruned=0, took_ms=0.0)
        return
    if not bank.enabled:
        _emit("index_done", bank.id, task_id=task.id, kind=task.kind,
              path=task.path, trigger=task.trigger, result="skipped",
              files_indexed=0, chunks_indexed=0, files_pruned=0, took_ms=0.0)
        return

    try:
        if task.kind == "file":
            _run_file(task, bank, running)
        elif task.kind in ("bulk", "rebuild"):
            _run_bulk(task, bank, rebuild=task.kind == "rebuild")
        elif task.kind == "prune":
            _run_prune(task, bank)
    except EmbeddingUnavailable as exc:
        # Soft degradation (NFR-10): the file keeps its old state, stays in
        # the hash diff, and the next pass retries it.
        _emit("index_error", bank.id, task_id=task.id, kind=task.kind,
              path=task.path, trigger=task.trigger, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - one bad task must not kill a worker
        log.exception("task %s (%s) failed", task.id, task.kind)
        _emit("index_error", bank.id, task_id=task.id, kind=task.kind,
              path=task.path, trigger=task.trigger,
              error=f"{type(exc).__name__}: {exc}")


def _worker_loop(name: str) -> None:
    while not _stop.is_set():
        task = _take()
        if task is None:
            continue
        running = _Running(task=task, started_at=time.time())
        with _lock:
            _running[name] = running
        _emit_queue(force=True)
        try:
            _execute(task, running)
        finally:
            with _lock:
                _running.pop(name, None)
            _emit_queue(force=True)


# ------------------------------------------------------------- lifecycle


def start(*, workers: int | None = None, on_event: Callable[[dict], None]) -> None:
    """Start the worker pool. Idempotent — a running queue is left alone."""
    global _on_event
    with _lock:
        if _workers:
            return
        _on_event = on_event
        _stop.clear()
        count = default_workers() if workers is None else max(1, int(workers))
        for i in range(count):
            name = f"mnemo-worker-{i}"
            thread = threading.Thread(
                target=_worker_loop, args=(name,), name=name, daemon=True
            )
            _workers.append(thread)
            thread.start()
    log.info("work queue started with %d worker(s)", len(_workers))


def stop(timeout: float = 10.0) -> None:
    """Signal the workers and wait. The task in flight finishes its batch."""
    global _on_event, _consecutive_high
    _stop.set()
    with _cv:
        _cv.notify_all()
    for thread in list(_workers) + list(_scanners):
        thread.join(timeout=timeout)
    with _lock:
        _workers.clear()
        _scanners.clear()
        _running.clear()
        _scanning.clear()
        _cancelled.clear()
        _consecutive_high = 0
        _on_event = None


def drop_bank(bank_id: str, *, timeout: float = 15.0) -> bool:
    """Cancel everything queued for a bank and wait for work in flight.

    Removing a bank while the worker holds a write connection to its index
    is the same class of race as removing it while a reader is open: on
    Windows the file simply cannot be unlinked.

    **Cancelling comes first, and it has to.** Purging the queue alone is not
    enough: a `bulk` scan runs on its own thread and streams new `file` tasks
    as it walks, so it refills the queue as fast as we empty it and the wait
    burns its whole timeout for nothing. The cancel flag stops the scanner at
    its next file and makes `enqueue` refuse anything further for this bank,
    which turns a race into a bounded wait for the one task already running.

    Returns True when the bank is quiet. On False the caller must call
    `resume_bank`, or the bank stays frozen for the rest of the process.
    """
    with _cv:
        _cancelled.add(bank_id)
        doomed = [t for t in _waiting.values() if t.bank_id == bank_id]
        for task in doomed:
            del _waiting[task.id]
            key = (task.bank_id, task.path) if task.path else None
            if key is not None and _by_file.get(key) == task.id:
                del _by_file[key]
        _cv.notify_all()
    if doomed:
        log.info("dropped %d queued task(s) for bank %s", len(doomed), bank_id)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not busy(bank_id):
            return True
        time.sleep(0.1)
    return not busy(bank_id)


def resume_bank(bank_id: str) -> None:
    """Undo `drop_bank`'s cancellation — for a removal that did not happen."""
    with _cv:
        _cancelled.discard(bank_id)


def is_cancelled(bank_id: str) -> bool:
    with _lock:
        return bank_id in _cancelled


def clear() -> None:
    """Drop everything still waiting. For tests and a clean shutdown path."""
    with _lock:
        _waiting.clear()
        _by_file.clear()
        _heap.clear()
