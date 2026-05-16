"""Single-file SQLite store: vectors (sqlite-vec) + FTS5 + change-state.

One file holds everything — including the per-file sha256 hashes
(Memory-design-v2 anti-pattern: NO separate hash manifest). The file is
disposable and fully rebuildable from the .md.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

from .config import DB_PATH, EMBEDDING_DIM


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the DB with sqlite-vec loaded and the schema ensured."""
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        f"""
        -- Change-state: file -> sha256. Lives in the DB, not a side manifest.
        CREATE TABLE IF NOT EXISTS files (
            path       TEXT PRIMARY KEY,
            sha256     TEXT NOT NULL,
            scope      TEXT NOT NULL,          -- 'project' | 'agent'
            agent_name TEXT                    -- NULL for project scope
        );

        -- One row per chunk (a section of a file).
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY,
            path        TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            scope       TEXT NOT NULL,
            agent_name  TEXT,
            heading     TEXT,
            content     TEXT NOT NULL,
            UNIQUE (path, chunk_index)
        );

        -- Dense vectors. rowid == chunks.id.
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            embedding float[{EMBEDDING_DIM}]
        );

        -- Sparse / lexical fallback (FTS5 is built into SQLite, ~zero infra).
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks USING fts5(content);
        """
    )
    conn.commit()


def get_indexed_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """Map of relpath -> sha256 for everything currently indexed."""
    return {
        r["path"]: r["sha256"]
        for r in conn.execute("SELECT path, sha256 FROM files")
    }


def insert_chunk(
    conn: sqlite3.Connection,
    path: str,
    chunk_index: int,
    scope: str,
    agent_name: str | None,
    heading: str,
    content: str,
    embedding: list[float],
) -> None:
    """Insert one chunk into the row table + vector + FTS indexes."""
    cur = conn.execute(
        "INSERT INTO chunks(path, chunk_index, scope, agent_name, heading, content) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (path, chunk_index, scope, agent_name, heading, content),
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


def set_file_hash(
    conn: sqlite3.Connection,
    path: str,
    sha256: str,
    scope: str,
    agent_name: str | None,
) -> None:
    """Record / update the sha256 of an indexed file."""
    conn.execute(
        "INSERT INTO files(path, sha256, scope, agent_name) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET "
        "sha256 = excluded.sha256, scope = excluded.scope, "
        "agent_name = excluded.agent_name",
        (path, sha256, scope, agent_name),
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
