"""Filesystem watcher: an edit becomes a queued reindex, with no command run.

This is what makes mnemo a service rather than a tool you remember to invoke
(Memory-contracts-v3 §5 phase 3, FR-3). It watches every enabled bank root
and turns real changes into `HIGH`-priority queue tasks.

Three filters sit between a raw event and the queue, each earning its place:

* **Debounce** (`MNEMO_DEBOUNCE_MS`, 800). One save is many events — editors
  write to a temp file and rename, formatters rewrite immediately after, and
  a `git checkout` touches hundreds of files at once. Collapsing a burst per
  path means one reindex per edit instead of one per syscall.
* **Hash confirmation.** watchdog fires on metadata touches and on writes
  that restore identical content. Comparing sha256 against what the index
  already holds turns "the file was touched" into "the file changed", which
  is the only thing worth embedding for.
* **Exclusion.** The bank's own `exclude` globs, applied through the same
  `index.path_is_excluded` the indexer uses, so the watcher and the walker
  can never disagree about what belongs in a bank.

Deletes and renames reach `prune` — a rename is handled as a delete of the
old path plus a create of the new one, because that is exactly what it is to
an index keyed by path.

A periodic rescan is the safety net. Watchdog can miss events (network
shares, a suspended laptop, a burst that overflows the OS buffer), and a
missed delete is invisible forever otherwise. The rescan is a `bulk` — scan
and hash-diff, no embedding — so an idle bank costs a directory walk.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from . import config, index, registry, store, workqueue
from .workqueue import Priority

log = logging.getLogger("mnemo.watcher")


def debounce_s() -> float:
    configured = getattr(config, "DEBOUNCE_MS", None)
    if configured is None:
        try:
            configured = int(os.environ.get("MNEMO_DEBOUNCE_MS", "800"))
        except ValueError:
            configured = 800
    return max(0.0, float(configured) / 1000.0)


def rescan_interval_s() -> float:
    """Safety-net full diff. 0 disables it."""
    configured = getattr(config, "RESCAN_INTERVAL_S", None)
    if configured is None:
        try:
            configured = float(os.environ.get("MNEMO_RESCAN_INTERVAL_S", "900"))
        except ValueError:
            configured = 900.0
    return max(0.0, float(configured))


# How often the flush thread wakes. Small enough that the debounce window is
# what actually decides latency, not the tick.
_TICK_S = 0.1

_lock = threading.RLock()
_observer: Observer | None = None
_thread: threading.Thread | None = None
_stop = threading.Event()
# (bank_id, relpath) -> deadline (monotonic). One entry per path collapses a
# storm of saves into a single reindex.
_pending: dict[tuple[str, str], float] = {}
# bank_id -> watchdog watch handle, so banks can be added and removed while
# the service runs.
_watches: dict[str, object] = {}
_last_rescan = 0.0


class _Handler(FileSystemEventHandler):
    """Translates watchdog events into debounced (bank, path) marks."""

    def __init__(self, bank_id: str, root: Path) -> None:
        self.bank_id = bank_id
        self.root = root

    def _mark(self, raw: str | bytes | None) -> None:
        if raw is None:
            return
        path = Path(os.fsdecode(raw))
        try:
            rel = path.resolve().relative_to(self.root).as_posix()
        except (ValueError, OSError):
            return  # outside the bank (or unresolvable) — not ours
        if not rel.endswith(".md"):
            return
        _touch(self.bank_id, rel)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._mark(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._mark(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            # A whole directory went away; enumerating what was in it means
            # asking the index, which is exactly what a bulk diff does.
            _rescan_bank(self.bank_id, trigger="watcher")
        else:
            self._mark(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        # A rename is a delete plus a create to a path-keyed index. Marking
        # both ends lets the normal hash check decide each one's fate.
        if event.is_directory:
            _rescan_bank(self.bank_id, trigger="watcher")
            return
        self._mark(event.src_path)
        self._mark(getattr(event, "dest_path", None))


def _touch(bank_id: str, rel: str) -> None:
    with _lock:
        _pending[(bank_id, rel)] = time.monotonic() + debounce_s()


def _rescan_bank(bank_id: str, *, trigger: str) -> None:
    try:
        workqueue.enqueue_bulk(bank_id, trigger=trigger)
    except Exception:  # noqa: BLE001 - a watcher must never die on a queue error
        log.exception("cannot enqueue bulk for bank %s", bank_id)


# --------------------------------------------------------------- flushing


def _indexed_hash(bank: registry.Bank, rel: str) -> str | None:
    """What the index believes this file's sha256 is, if anything."""
    if not bank.db_path.exists():
        return None
    conn = None
    try:
        conn = store.connect(bank.db_path, ensure=False)
        row = store.get_file_row(conn, rel)
        return row["sha256"] if row else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        if conn is not None:
            conn.close()


def _flush_one(bank: registry.Bank, rel: str) -> None:
    """Decide what one settled path deserves: index, prune, or nothing."""
    if index.path_is_excluded(rel, bank.exclude):
        return
    abs_path = bank.root / rel
    known = _indexed_hash(bank, rel)

    if not abs_path.is_file():
        if known is not None:
            workqueue.enqueue_prune(bank.id, rel, priority=Priority.HIGH,
                                    trigger="watcher")
        return

    try:
        digest = workqueue.file_sha256(abs_path)
    except OSError:
        return  # still being written; the next event will bring it back
    if digest == known:
        return  # touched, not changed — the expensive path stops here
    workqueue.enqueue_file(bank.id, rel, priority=Priority.HIGH,
                           trigger="watcher")


def _flush_due() -> None:
    now = time.monotonic()
    with _lock:
        due = [key for key, deadline in _pending.items() if deadline <= now]
        for key in due:
            del _pending[key]
    if not due:
        return
    banks = {b.id: b for b in registry.load()}
    for bank_id, rel in due:
        bank = banks.get(bank_id)
        if bank is None or not bank.enabled:
            continue
        try:
            _flush_one(bank, rel)
        except Exception:  # noqa: BLE001
            log.exception("cannot handle change in %s: %s", bank_id, rel)


def _sync_watches() -> None:
    """Add and drop watches so a bank registered at runtime is watched."""
    observer = _observer
    if observer is None:
        return
    try:
        banks = {b.id: b for b in registry.load() if b.enabled and b.exists}
    except Exception:  # noqa: BLE001
        return
    with _lock:
        for bank_id in list(_watches):
            if bank_id not in banks:
                try:
                    observer.unschedule(_watches.pop(bank_id))
                except Exception:  # noqa: BLE001
                    _watches.pop(bank_id, None)
        for bank_id, bank in banks.items():
            if bank_id in _watches:
                continue
            try:
                root = bank.root.resolve()
                _watches[bank_id] = observer.schedule(
                    _Handler(bank_id, root), str(root), recursive=True
                )
                log.info("watching %s (%s)", bank.name, root)
            except Exception:  # noqa: BLE001
                log.exception("cannot watch %s", bank.root)


def _loop() -> None:
    global _last_rescan
    _last_rescan = time.monotonic()
    while not _stop.wait(_TICK_S):
        try:
            _flush_due()
            _sync_watches()
            interval = rescan_interval_s()
            if interval and time.monotonic() - _last_rescan >= interval:
                _last_rescan = time.monotonic()
                for bank in registry.load():
                    if bank.enabled and bank.exists:
                        _rescan_bank(bank.id, trigger="watcher")
        except Exception:  # noqa: BLE001 - the loop outlives any one failure
            log.exception("watcher tick failed")


# -------------------------------------------------------------- lifecycle


def start() -> None:
    """Begin watching every enabled bank. Idempotent."""
    global _observer, _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _observer = Observer()
        _observer.daemon = True
        _observer.start()
        _thread = threading.Thread(
            target=_loop, name="mnemo-watcher", daemon=True
        )
        _thread.start()
    _sync_watches()
    log.info("watcher started (debounce %.0f ms)", debounce_s() * 1000)


def stop(timeout: float = 5.0) -> None:
    global _observer, _thread
    _stop.set()
    thread, observer = _thread, _observer
    if thread is not None:
        thread.join(timeout=timeout)
    if observer is not None:
        try:
            observer.stop()
            observer.join(timeout=timeout)
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        _watches.clear()
        _pending.clear()
    _thread, _observer = None, None


def pending() -> int:
    """Paths currently inside their debounce window (tests, diagnostics)."""
    with _lock:
        return len(_pending)
