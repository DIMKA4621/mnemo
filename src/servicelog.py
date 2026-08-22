"""Service journal — what was asked, and what was indexed.

``STATE_DIR/service.db`` (Memory-contracts-v3 §7). Two event kinds, one file,
written **only by the backend** through one connection, so there is a single
writer and no lock contention to reason about.

Two rules shape the whole module:

* **Telemetry never breaks the thing it observes.** Every ``log_*`` swallows
  its own errors; a failed write costs a log line, not a search.
* **Both halves of a query are recorded** — the request *and* the sections
  that went back (``hits_json``), because "what did mnemo actually return for
  that prompt" is the question the console exists to answer.

Retention is ``$MNEMO_LOG_RETENTION_DAYS`` (30), pruned at start and every six
hours, with a row-count backstop for a machine that queries far more than it
sleeps.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS query_events (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    ts_epoch    REAL NOT NULL,
    bank_id     TEXT NOT NULL,
    face        TEXT NOT NULL,
    query       TEXT NOT NULL,
    path_prefix TEXT,
    status      TEXT NOT NULL,
    n_hits      INTEGER NOT NULL,
    took_ms     REAL NOT NULL,
    hits_json   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qe_bank_ts ON query_events(bank_id, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_qe_ts      ON query_events(ts_epoch DESC);

CREATE TABLE IF NOT EXISTS index_events (
    id             INTEGER PRIMARY KEY,
    ts             TEXT NOT NULL,
    ts_epoch       REAL NOT NULL,
    bank_id        TEXT NOT NULL,
    kind           TEXT NOT NULL,
    trigger        TEXT NOT NULL,
    path           TEXT,
    result         TEXT NOT NULL,
    files_indexed  INTEGER NOT NULL DEFAULT 0,
    chunks_indexed INTEGER NOT NULL DEFAULT 0,
    files_pruned   INTEGER NOT NULL DEFAULT 0,
    took_ms        REAL,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ie_bank_ts ON index_events(bank_id, ts_epoch DESC);
CREATE INDEX IF NOT EXISTS idx_ie_ts      ON index_events(ts_epoch DESC);
"""

TABLES = {"query": "query_events", "index": "index_events"}

# VACUUM is not free; run it at most daily and only after a prune that
# actually reclaimed something worth reclaiming (§7.4).
_VACUUM_MIN_ROWS = 10_000
_VACUUM_MIN_INTERVAL_S = 24 * 3600

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None
_deleted_since_vacuum = 0
_last_vacuum = 0.0

_pruner: threading.Thread | None = None
_pruner_stop = threading.Event()
# Read connections live per thread so a long console query never queues
# behind — or in front of — the writer.
_readers = threading.local()


# --------------------------------------------------------------- settings


def db_path() -> Path:
    """Resolved per call, and through ``config`` rather than an import-time
    binding — otherwise moving ``config.STATE_DIR`` leaves the journal writing
    to the directory that was current when this module was first imported."""
    return Path(config.STATE_DIR) / "service.db"


def default_retention_days() -> int:
    configured = getattr(config, "LOG_RETENTION_DAYS", None)
    if configured is not None:
        return int(configured)
    return _int_env("MNEMO_LOG_RETENTION_DAYS", 30)


def default_max_rows() -> int:
    configured = getattr(config, "LOG_MAX_ROWS", None)
    if configured is not None:
        return int(configured)
    return _int_env("MNEMO_LOG_MAX_ROWS", 200_000)


def prune_interval_s() -> float:
    configured = getattr(config, "LOG_PRUNE_INTERVAL_S", None)
    return float(configured) if configured else 6 * 3600.0


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _now() -> tuple[str, float]:
    now = datetime.now(timezone.utc).astimezone()
    return now.isoformat(timespec="milliseconds"), now.timestamp()


# ------------------------------------------------------------- connection


def connect() -> sqlite3.Connection:
    """The one journal connection. Shared across threads under ``_lock``."""
    global _conn, _conn_path
    with _lock:
        path = db_path()
        if _conn is not None and _conn_path == path:
            return _conn
        if _conn is not None:
            _conn.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=10.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        _conn, _conn_path = conn, path
        return conn


def close() -> None:
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn, _conn_path = None, None


def _reader() -> sqlite3.Connection:
    """A per-thread read connection, separate from the writer's.

    Reads used to share the writer's connection and therefore the writer's
    lock, so one console query for a thousand log rows serialised every
    ``log_query`` behind it — and ``prune`` could hold that lock through a
    ``VACUUM``. WAL lets readers run beside the writer, so the only thing
    that was actually serialising them was us.
    """
    path = db_path()
    cached = getattr(_readers, "conn", None)
    if cached is not None and getattr(_readers, "path", None) == path:
        return cached
    if cached is not None:
        cached.close()
    connect()  # make sure the file and schema exist before reading
    conn = sqlite3.connect(path, timeout=10.0)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    _readers.conn, _readers.path = conn, path
    return conn


# ------------------------------------------------------------------ write


def _hit_json(hit: Any) -> dict[str, Any]:
    """Serialise one hit for ``hits_json``.

    Read by attribute rather than by type so the journal does not import
    ``search`` — the log has no business pinning the search module's shape.
    """
    return {
        "chunk_uid": getattr(hit, "chunk_uid", None),
        "path": getattr(hit, "path", None),
        "heading": getattr(hit, "heading", None),
        "chunk_index": getattr(hit, "chunk_index", None),
        "score": getattr(hit, "score", None),
        "sim": getattr(hit, "sim", None),
        "content": getattr(hit, "content", None),
    }


def log_query(
    *,
    bank_id: str,
    face: str,
    query: str,
    path_prefix: str | None,
    status: str,
    hits: Sequence[Any],
    took_ms: float,
) -> None:
    """Record one search: the request and every section it returned."""
    try:
        ts, epoch = _now()
        payload = json.dumps(
            [_hit_json(h) for h in hits], ensure_ascii=False
        )
        with _lock:
            conn = connect()
            conn.execute(
                "INSERT INTO query_events(ts, ts_epoch, bank_id, face, query, "
                "path_prefix, status, n_hits, took_ms, hits_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts, epoch, bank_id, face, query, path_prefix, status,
                    len(hits), float(took_ms), payload,
                ),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 - telemetry must never break a search
        pass


def log_index(
    *,
    bank_id: str,
    kind: str,
    trigger: str,
    path: str | None,
    result: str,
    files_indexed: int = 0,
    chunks_indexed: int = 0,
    files_pruned: int = 0,
    took_ms: float | None = None,
    error: str | None = None,
) -> None:
    """Record one indexing event — a single file or a bulk pass."""
    try:
        ts, epoch = _now()
        with _lock:
            conn = connect()
            conn.execute(
                "INSERT INTO index_events(ts, ts_epoch, bank_id, kind, trigger, "
                "path, result, files_indexed, chunks_indexed, files_pruned, "
                "took_ms, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts, epoch, bank_id, kind, trigger, path, result,
                    files_indexed, chunks_indexed, files_pruned,
                    None if took_ms is None else float(took_ms), error,
                ),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 - telemetry must never break indexing
        pass


# ------------------------------------------------------------------- read


def _where(
    *,
    bank_id: str | None,
    since: float | None,
    until: float | None,
    kind: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if bank_id:
        clauses.append("bank_id = ?")
        args.append(bank_id)
    if since is not None:
        clauses.append("ts_epoch >= ?")
        args.append(float(since))
    if until is not None:
        clauses.append("ts_epoch <= ?")
        args.append(float(until))
    if kind:
        clauses.append("kind = ?")
        args.append(kind)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", args


def read_queries(
    *,
    bank_id: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    """Newest first. ``hits_json`` comes back unpacked as ``hits``."""
    where, args = _where(bank_id=bank_id, since=since, until=until)
    rows = _reader().execute(
        f"SELECT * FROM query_events{where} "
        f"ORDER BY ts_epoch DESC, id DESC LIMIT ? OFFSET ?",
        (*args, int(limit), int(offset)),
    ).fetchall()
    events = []
    for row in rows:
        event = dict(row)
        raw = event.pop("hits_json", "[]")
        try:
            event["hits"] = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            event["hits"] = []
        events.append(event)
    return events


def read_index(
    *,
    bank_id: str | None = None,
    since: float | None = None,
    until: float | None = None,
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    """Newest first."""
    where, args = _where(bank_id=bank_id, since=since, until=until, kind=kind)
    rows = _reader().execute(
        f"SELECT * FROM index_events{where} "
        f"ORDER BY ts_epoch DESC, id DESC LIMIT ? OFFSET ?",
        (*args, int(limit), int(offset)),
    ).fetchall()
    return [dict(r) for r in rows]


def count(table: str, **filters: Any) -> int:
    """Row count for ``query`` / ``index`` (or the raw table name)."""
    name = TABLES.get(table, table)
    if name not in TABLES.values():
        raise ValueError(f"unknown table {table!r}")
    where, args = _where(
        bank_id=filters.get("bank_id"),
        since=filters.get("since"),
        until=filters.get("until"),
        kind=filters.get("kind") if name == "index_events" else None,
    )
    row = _reader().execute(
        f"SELECT count(*) AS n FROM {name}{where}", args
    ).fetchone()
    return int(row["n"])


def last_index_error(bank_id: str) -> str | None:
    """Most recent failure for a bank — what ``BankInfo.last_error`` shows."""
    try:
        row = _reader().execute(
            "SELECT result, error FROM index_events WHERE bank_id = ? "
            "AND kind != 'bulk' ORDER BY ts_epoch DESC, id DESC LIMIT 1",
            (bank_id,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    # Latest-event-wins, which IS the lifecycle: an error sets the field and
    # the next successful index for that bank clears it. Reporting "the most
    # recent error ever" instead would leave the console's red badge lit
    # forever, long after the cause was fixed.
    if row is None or row["result"] != "error":
        return None
    return row["error"]


# -------------------------------------------------------------- retention


def prune(
    retention_days: int | None = None, max_rows: int | None = None
) -> tuple[int, int]:
    """Drop events past the retention window. Returns (queries, index) deleted.

    The row backstop is second, so a burst that overshoots the cap inside the
    window still gets trimmed instead of growing the file forever.
    """
    global _deleted_since_vacuum, _last_vacuum
    keep_days = (
        default_retention_days() if retention_days is None else int(retention_days)
    )
    cap = default_max_rows() if max_rows is None else int(max_rows)
    deleted = {"query_events": 0, "index_events": 0}

    with _lock:
        conn = connect()
        if keep_days > 0:
            cutoff = time.time() - keep_days * 86400
            for table in deleted:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE ts_epoch < ?", (cutoff,)
                )
                deleted[table] += cur.rowcount or 0
        if cap > 0:
            for table in deleted:
                cur = conn.execute(
                    # id breaks the tie: many events can share a millisecond,
                    # and "keep the newest N" must be deterministic.
                    f"DELETE FROM {table} WHERE id NOT IN "
                    f"(SELECT id FROM {table} "
                    f"ORDER BY ts_epoch DESC, id DESC LIMIT ?)",
                    (cap,),
                )
                deleted[table] += cur.rowcount or 0
        conn.commit()

        total = deleted["query_events"] + deleted["index_events"]
        _deleted_since_vacuum += total
        now = time.time()
        if (
            _deleted_since_vacuum > _VACUUM_MIN_ROWS
            and now - _last_vacuum > _VACUUM_MIN_INTERVAL_S
        ):
            conn.execute("VACUUM")
            _deleted_since_vacuum = 0
            _last_vacuum = now

    return deleted["query_events"], deleted["index_events"]


def start_pruner(interval_s: float | None = None) -> None:
    """Prune now, then keep pruning in the background (§7.4)."""
    global _pruner
    with _lock:
        if _pruner is not None and _pruner.is_alive():
            return
        _pruner_stop.clear()
        every = prune_interval_s() if interval_s is None else float(interval_s)

        def _loop() -> None:
            while not _pruner_stop.wait(every):
                try:
                    prune()
                except Exception:  # noqa: BLE001 - never kill the thread
                    pass

        _pruner = threading.Thread(
            target=_loop, name="mnemo-log-pruner", daemon=True
        )
        _pruner.start()
    prune()


def stop_pruner() -> None:
    global _pruner
    _pruner_stop.set()
    thread = _pruner
    if thread is not None:
        thread.join(timeout=2.0)
    _pruner = None
