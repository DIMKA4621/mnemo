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


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the bank DB with sqlite-vec loaded and the schema ensured.

    Concurrency: parallel Claude Code sessions, the MCP server, and the
    hooks all open the same file. WAL lets readers and a writer coexist;
    ``busy_timeout`` makes the rare DDL / writer-vs-writer collision wait
    instead of failing with ``database is locked``.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
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


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the schema, dropping an incompatible one first.

    A v2 database (scope / agent_name columns, no ``meta``) or any future
    layout mismatch is wiped here rather than migrated. Nothing is lost: the
    next reconcile finds an empty change-state and re-derives everything from
    the .md — which is the documented migration path for v3.
    """
    existing = {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if existing & set(_ALL_TABLES) and _read_meta(conn).get(
        "schema_version"
    ) != SCHEMA_VERSION:
        for table in _ALL_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {table}")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta ("
        "    key   TEXT PRIMARY KEY,"
        "    value TEXT NOT NULL)"
    )
    _set(conn, "schema_version", SCHEMA_VERSION)
    _create_content(conn, EMBEDDING_DIM)
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


def reset_index(conn: sqlite3.Connection) -> None:
    """Drop every chunk, vector, FTS row and hash; keep the meta.

    DROP rather than DELETE: a rebuild may be triggered by a dimensionality
    change, and the ``vec0`` column width is part of the table definition.
    """
    for table in _CONTENT_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    dim = int(_read_meta(conn).get("embedding_dim") or EMBEDDING_DIM)
    _create_content(conn, dim)
    conn.commit()


# ----------------------------------------------------------------- write


def get_indexed_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """Map of relpath -> sha256 for everything currently indexed."""
    return {
        r["path"]: r["sha256"]
        for r in conn.execute("SELECT path, sha256 FROM files")
    }


def get_file_row(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()


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
        for r in conn.execute(
            f"SELECT rowid, vec_to_json(embedding) AS j "
            f"FROM vec_chunks WHERE rowid IN ({placeholders})",
            ids,
        )
    }


def chunk_count(conn: sqlite3.Connection) -> int:
    """Total chunks — 0 is what makes a bank's status ``empty``, not ``ready``."""
    return conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]


def file_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) AS n FROM files").fetchone()["n"]


def list_files(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM files ORDER BY path").fetchall()


def chunk_map(conn: sqlite3.Connection, path: str) -> list[sqlite3.Row]:
    """Chunk boundaries of one file, in order — the UI's chunk-viz source."""
    return conn.execute(
        "SELECT chunk_uid, chunk_index, heading, start_char, end_char "
        "FROM chunks WHERE path = ? ORDER BY chunk_index",
        (path,),
    ).fetchall()
