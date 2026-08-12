"""Single-file SQLite store: vectors (sqlite-vec) + FTS5 + change-state.

One file per bank holds everything — including the per-file sha256 hashes
(Memory-design-v2 anti-pattern: NO separate hash manifest). The file is
disposable and fully rebuildable from the .md.

v3 (Memory-contracts-v3 §3): the bank is flat — no ``scope`` / ``agent_name``
columns, because isolation is one bank per boundary, not sub-scopes inside a
bank. A ``meta`` table records the schema version, the bank identity and the
provider key that built the vectors, so an incompatible index is rebuilt from
the .md instead of being read as if it still matched.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlite_vec

from .config import EMBEDDING_DIM

# Bumped whenever the table layout changes. A DB carrying anything else is
# not migrated — it is dropped and rebuilt from the .md (the index is derived
# data; a converter would be code we maintain forever for no gain).
SCHEMA_VERSION = "3"

# Content tables — everything a rebuild throws away. ``meta`` survives.
_CONTENT_TABLES = ("vec_chunks", "fts_chunks", "chunks", "files")
_ALL_TABLES = _CONTENT_TABLES + ("meta",)


def _now() -> str:
    """ISO-8601 with offset — the timestamp format used across the service."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def chunk_uid(path: str, chunk_index: int) -> str:
    """Deterministic, machine-independent id for one chunk.

    Content-independent on purpose: the same section of the same file keeps
    its uid across reindexes and across machines, which is what the UI needs
    to highlight a chunk and what the log needs to point at a returned
    section. ``path`` must be a POSIX relpath from the bank root.
    """
    return hashlib.sha1(
        f"{path}\x00{chunk_index}".encode("utf-8")
    ).hexdigest()[:16]


class VectorExtensionUnavailable(RuntimeError):
    """This interpreter cannot load SQLite extensions, so sqlite-vec cannot.

    Not something a retry or a reinstall fixes: the interpreter was built
    without ``--enable-loadable-sqlite-extensions``, and then
    ``enable_load_extension`` does not exist at all. Observed on the
    python.org / actions-setup-python macOS builds; Homebrew's python and the
    ordinary Linux and Windows builds are fine.

    It has to be said in full because `import sqlite_vec` still SUCCEEDS on
    such a Python — so an installer that probes by importing reports a clean
    install, and the first search is what finally fails, several layers deep.
    """


def load_vec(conn: sqlite3.Connection) -> None:
    """Load sqlite-vec into an open connection, or say why it cannot."""
    if not hasattr(conn, "enable_load_extension"):
        raise VectorExtensionUnavailable(
            "this Python was built without loadable SQLite extensions, so "
            "sqlite-vec cannot load and no bank can be opened. Install mnemo "
            "under a Python that has them (on macOS: Homebrew's "
            "`python@3.12` -- the python.org build does not), then re-run "
            "the installer."
        )
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def vector_support() -> str | None:
    """``None`` when a bank can be opened here, else the reason it cannot.

    For `doctor` and both installers: a check that answers before an index
    exists, on a throwaway in-memory database, so it costs nothing and works
    on a machine that has never indexed anything.
    """
    conn = sqlite3.connect(":memory:")
    try:
        load_vec(conn)
    except VectorExtensionUnavailable as exc:
        return str(exc)
    except Exception as exc:  # a broken wheel, a missing .so, anything else
        return f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return None


def connect(
    db_path: Path, *, ensure: bool = True, dim: int | None = None
) -> sqlite3.Connection:
    """Open the bank DB with sqlite-vec loaded.

    Concurrency: parallel Claude Code sessions, the MCP server, the hooks and
    the backend all open the same file. WAL lets readers and a writer coexist;
    ``busy_timeout`` makes the rare DDL / writer-vs-writer collision wait
    instead of failing with ``database is locked``.

    ``ensure=False`` opens **without any DDL** — the read path. Opening must
    not write: a reader that takes the single WAL writer lock contends with
    the indexer on every search, and on a database that is genuinely
    read-only (permissions, an antivirus lock, a read-only mount) the write
    fails and the bank looks *empty* rather than unreadable — which tells
    every agent to stop asking about a bank that is actually full.

    With ``ensure=True`` the schema is created only when it is missing or
    outdated; a database already at the current version is opened with reads
    alone (see ``ensure_schema``), so the default stays cheap and lock-free.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    load_vec(conn)
    conn.row_factory = sqlite3.Row
    if ensure:
        ensure_schema(conn, dim=dim)
    return conn


# ---------------------------------------------------------------- schema


def _create_content(conn: sqlite3.Connection, dim: int) -> None:
    conn.executescript(
        f"""
        -- Change-state: file -> sha256. Lives in the DB, not a side manifest.
        -- size / mtime_ns are diagnostics only: the ONLY source of truth for
        -- "did this change" is sha256 (an mtime fast-path is a bug farm on
        -- filesystems with coarse timestamps).
        CREATE TABLE IF NOT EXISTS files (
            path       TEXT PRIMARY KEY,   -- POSIX relpath from the bank root
            sha256     TEXT NOT NULL,
            size       INTEGER NOT NULL,
            mtime_ns   INTEGER NOT NULL,
            n_chunks   INTEGER NOT NULL DEFAULT 0,
            indexed_at TEXT NOT NULL
        );

        -- One row per chunk (a section of a file). ``path`` is what the
        -- optional path_prefix filter narrows on — no extra metadata needed.
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY,   -- joins vec_chunks / fts_chunks
            chunk_uid   TEXT NOT NULL UNIQUE,  -- deterministic, portable
            path        TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            heading     TEXT,
            content     TEXT NOT NULL,
            start_char  INTEGER NOT NULL,      -- offset in the source file
            end_char    INTEGER NOT NULL,      -- exclusive
            UNIQUE (path, chunk_index)
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);

        -- Dense vectors. rowid == chunks.id.
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            embedding float[{dim}]
        );

        -- Sparse / lexical fallback (FTS5 is built into SQLite, ~zero infra).
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(content);
        """
    )


class SchemaTooNew(RuntimeError):
    """The database was written by a newer mnemo than this one."""


def _schema_int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def ensure_schema(conn: sqlite3.Connection, *, dim: int | None = None) -> None:
    """Create the schema, dropping an OLDER incompatible one first.

    A v2 database (scope / agent_name columns, no ``meta``) or any older
    layout is wiped rather than migrated. Nothing is lost: the next reconcile
    finds an empty change-state and re-derives everything from the .md, which
    is the documented migration path for v3.

    A **newer** schema is never touched. One engine directory is shared by
    every project on the machine, so an older build meeting an index written
    by a newer one is a normal consequence of a rollback or of two phases in
    flight — and silently destroying that index would be the worst possible
    response. It raises instead.

    Fast path: an already-current database is detected with reads only and
    returns without opening a write transaction. That is what keeps a search
    from contending with the indexer for the WAL writer lock, and what lets a
    read-only database be opened at all.
    """
    existing = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    stored = _read_meta(conn).get("schema_version") if "meta" in existing else None
    current = _schema_int(SCHEMA_VERSION)
    found = _schema_int(stored)

    if stored is not None and found is not None and current is not None:
        if found > current:
            raise SchemaTooNew(
                f"index schema v{found} was written by a newer mnemo "
                f"(this build speaks v{current}); refusing to touch it"
            )
        if found == current and _ALL_TABLES[0] in existing:
            return  # already current — no DDL, no write, no lock

    if existing & set(_ALL_TABLES) and stored != SCHEMA_VERSION:
        for table in _ALL_TABLES:  # older or pre-meta layout -> rebuild
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta ("
        "    key   TEXT PRIMARY KEY,"
        "    value TEXT NOT NULL)"
    )
    _set(conn, "schema_version", SCHEMA_VERSION)
    # The vector column width belongs to whoever produces the vectors. Fall
    # back to what the index already recorded, and only then to the built-in
    # default — the store must not assume the local provider (phase 7).
    width = dim or _schema_int(_read_meta(conn).get("embedding_dim")) or EMBEDDING_DIM
    _create_content(conn, width)
    conn.commit()


# ------------------------------------------------------------------ meta


def _read_meta(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
    except sqlite3.OperationalError:
        return {}  # pre-v3 database: no meta table at all


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(conn: sqlite3.Connection) -> dict[str, str]:
    """All index metadata as a flat dict."""
    return _read_meta(conn)


def probe(db_path: Path) -> dict[str, Any]:
    """Read what an index file says about itself, without opening it for use.

    Triage, not use — which is why this does not go through ``connect``. That
    one loads sqlite-vec and sets ``journal_mode=WAL``, and setting the
    journal mode is a *write*: it would leave a fresh ``-wal`` beside a file
    the caller is about to report as debris, and it would fail outright on a
    database that is genuinely read-only. Here the file is opened ``mode=ro``
    and only two things are asked of it — what ``meta`` records, and how many
    files it indexed.

    ``mode=ro`` is not the same as "touches nothing", and the difference is
    observable: opening a database whose ``-wal`` is stale or malformed makes
    SQLite remove that sibling as part of recovery, read-only or not. It is
    not a new hazard — every ordinary search opens the same file and does the
    same thing — but a caller that measures a file's on-disk footprint must
    measure it *before* probing, or it will report a size that the probe
    itself changed.

    Every failure is returned rather than raised. The callers are diagnostics
    (``doctor``, ``clean-orphans``); an unreadable file is a fact to print,
    not a reason to abort the listing. Keys: ``meta`` (possibly empty — a v2
    database has no ``meta`` table at all), ``files`` (``None`` when the table
    is missing) and ``error``.
    """
    meta: dict[str, str] = {}
    files: int | None = None
    error: str | None = None
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "meta" in tables:
                meta = {
                    str(k): str(v)
                    for k, v in conn.execute("SELECT key, value FROM meta")
                }
            if "files" in tables:
                files = int(conn.execute("SELECT count(*) FROM files").fetchone()[0])
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        error = str(exc)
    return {"meta": meta, "files": files, "error": error}


def init_meta(
    conn: sqlite3.Connection,
    *,
    bank_id: str,
    bank_root: str,
    provider_key: str,
    dim: int,
) -> None:
    """Bind this index to a bank and a provider. Idempotent.

    ``created_at`` is written once; everything else is refreshed, so calling
    this after ``needs_rebuild`` leaves the meta describing what the index is
    about to become.
    """
    meta = _read_meta(conn)
    if "created_at" not in meta:
        _set(conn, "created_at", _now())
    _set(conn, "bank_id", bank_id)
    _set(conn, "bank_root", bank_root)
    _set(conn, "provider_key", provider_key)
    _set(conn, "embedding_dim", str(dim))
    conn.commit()


def mark_indexed(conn: sqlite3.Connection) -> None:
    """Stamp ``last_indexed_at`` — called at the end of every reconcile."""
    _set(conn, "last_indexed_at", _now())


def needs_rebuild(
    conn: sqlite3.Connection, *, provider_key: str, dim: int
) -> bool:
    """True when the stored vectors are not comparable to new ones.

    Vectors from two different models must never share a database — they are
    not comparable and the sqlite-vec column has a fixed width. A fresh index
    (no provider recorded yet) needs no rebuild: there is nothing in it.
    """
    meta = _read_meta(conn)
    if meta.get("schema_version") != SCHEMA_VERSION:
        return True
    if "provider_key" not in meta:
        return False
    return meta["provider_key"] != provider_key or meta.get(
        "embedding_dim"
    ) != str(dim)


def reset_index(conn: sqlite3.Connection, *, dim: int | None = None) -> None:
    """Drop every chunk, vector, FTS row and hash; keep the meta.

    DROP rather than DELETE: a rebuild may be triggered by a dimensionality
    change, and the ``vec0`` column width is part of the table definition.

    Pass ``dim`` — the width of the vectors that are about to be written — so
    this can run **before** ``init_meta`` claims the new provider. Order
    matters: if meta were updated first and the process died before the wipe,
    meta would claim provider B over provider A's vectors, ``needs_rebuild``
    would answer False forever, and search would silently blend incomparable
    vectors. Wiping first is recoverable; the reverse is not. ``dim=None``
    falls back to what meta already records, for callers rebuilding in place.
    """
    for table in _CONTENT_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    width = dim or _schema_int(_read_meta(conn).get("embedding_dim")) or EMBEDDING_DIM
    _create_content(conn, width)
    conn.commit()


# ----------------------------------------------------------------- write


def _query(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    """Run a read query, treating "the schema is not there yet" as empty.

    A reader may legitimately open a database that no writer has initialised
    (``connect(..., ensure=False)``, or one that is read-only). "No rows" is
    the honest answer there; anything else — a corrupt file, a locked page —
    still raises, because that is not the same thing as empty.
    """
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise


def get_indexed_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """Map of relpath -> sha256 for everything currently indexed."""
    return {
        r["path"]: r["sha256"]
        for r in _query(conn, "SELECT path, sha256 FROM files")
    }


def get_file_row(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    rows = _query(conn, "SELECT * FROM files WHERE path = ?", (path,))
    return rows[0] if rows else None


def insert_chunk(
    conn: sqlite3.Connection,
    *,
    chunk_uid: str,
    path: str,
    chunk_index: int,
    heading: str,
    content: str,
    start_char: int,
    end_char: int,
    embedding: list[float],
) -> int:
    """Insert one chunk into the row table + vector + FTS indexes."""
    cur = conn.execute(
        "INSERT INTO chunks(chunk_uid, path, chunk_index, heading, content, "
        "start_char, end_char) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chunk_uid, path, chunk_index, heading, content, start_char, end_char),
    )
    cid = cur.lastrowid
    conn.execute(
        "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
        (cid, sqlite_vec.serialize_float32(embedding)),
    )
    conn.execute(
        "INSERT INTO fts_chunks(rowid, content) VALUES (?, ?)",
        (cid, content),
    )
    return cid


def set_file_hash(
    conn: sqlite3.Connection,
    *,
    path: str,
    sha256: str,
    size: int,
    mtime_ns: int,
    n_chunks: int,
) -> None:
    """Record / update the change-state of an indexed file."""
    conn.execute(
        "INSERT INTO files(path, sha256, size, mtime_ns, n_chunks, indexed_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "sha256 = excluded.sha256, size = excluded.size, "
        "mtime_ns = excluded.mtime_ns, n_chunks = excluded.n_chunks, "
        "indexed_at = excluded.indexed_at",
        (path, sha256, size, mtime_ns, n_chunks, _now()),
    )


def delete_file(conn: sqlite3.Connection, path: str) -> None:
    """Remove a file and all its chunks from EVERY index (prune)."""
    ids = [
        r["id"]
        for r in conn.execute("SELECT id FROM chunks WHERE path = ?", (path,))
    ]
    for cid in ids:
        conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (cid,))
        conn.execute("DELETE FROM fts_chunks WHERE rowid = ?", (cid,))
    conn.execute("DELETE FROM chunks WHERE path = ?", (path,))
    conn.execute("DELETE FROM files WHERE path = ?", (path,))


# ------------------------------------------------------------------ read


def get_vectors(
    conn: sqlite3.Connection, ids: list[int]
) -> dict[int, list[float]]:
    """Stored embeddings for the given chunk ids (for the weak-match gate).

    Uses sqlite-vec's documented ``vec_to_json`` so we never depend on the
    raw blob layout.
    """
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    return {
        r["rowid"]: json.loads(r["j"])
        for r in _query(
            conn,
            f"SELECT rowid, vec_to_json(embedding) AS j "
            f"FROM vec_chunks WHERE rowid IN ({placeholders})",
            tuple(ids),
        )
    }


def chunk_count(conn: sqlite3.Connection) -> int:
    """Total chunks — 0 is what makes a bank's status ``empty``, not ``ready``."""
    rows = _query(conn, "SELECT count(*) AS n FROM chunks")
    return rows[0]["n"] if rows else 0


def file_count(conn: sqlite3.Connection) -> int:
    rows = _query(conn, "SELECT count(*) AS n FROM files")
    return rows[0]["n"] if rows else 0


def list_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return _query(conn, "SELECT * FROM files ORDER BY path")


def chunk_map(conn: sqlite3.Connection, path: str) -> list[sqlite3.Row]:
    """Chunk boundaries of one file, in order — the UI's chunk-viz source."""
    return _query(
        conn,
        "SELECT chunk_uid, chunk_index, heading, start_char, end_char "
        "FROM chunks WHERE path = ? ORDER BY chunk_index",
        (path,),
    )
