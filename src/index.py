"""One-way sync: .md -> vector index.

Walk the memory tree, hash-diff against the state in the DB, reindex only
changed files, and prune anything that disappeared (delete / rename).

Idempotent and deterministic: deleting memory.db and re-running yields the
exact same state. The .md files are the single source of truth.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .chunker import split_markdown
from .config import AGENT_MEMORY_DIR, PROJECT_MEMORY_DIR, ROOT_DIR
from .embedder import embed_passages
from .store import (
    connect,
    delete_file,
    get_indexed_hashes,
    insert_chunk,
    set_file_hash,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _discover() -> list[tuple[Path, str, str | None]]:
    """Every memory .md as (path, scope, agent_name). Sorted = deterministic."""
    found: list[tuple[Path, str, str | None]] = []
    if PROJECT_MEMORY_DIR.exists():
        for p in sorted(PROJECT_MEMORY_DIR.rglob("*.md")):
            found.append((p, "project", None))
    if AGENT_MEMORY_DIR.exists():
        agents = sorted(d for d in AGENT_MEMORY_DIR.iterdir() if d.is_dir())
        for agent_dir in agents:
            for p in sorted(agent_dir.rglob("*.md")):
                found.append((p, "agent", agent_dir.name))
    return found


def reindex(verbose: bool = True) -> None:
    """Full reconcile: changed files reindexed, removed files pruned."""
    conn = connect()
    try:
        disk = {
            str(p.relative_to(ROOT_DIR)): (p, scope, agent)
            for p, scope, agent in _discover()
        }
        indexed = get_indexed_hashes(conn)

        # Prune: indexed but no longer on disk (deleted or renamed).
        for gone in sorted(set(indexed) - set(disk)):
            delete_file(conn, gone)
            if verbose:
                print(f"pruned  {gone}")

        # Add / update only changed files.
        for relpath, (path, scope, agent) in disk.items():
            digest = _sha256(path)
            if indexed.get(relpath) == digest:
                continue  # unchanged
            delete_file(conn, relpath)  # clean reindex (prune-on-edit too)
            chunks = split_markdown(path.read_text(encoding="utf-8"))
            vectors = embed_passages([c.text for c in chunks])
            for chunk, vec in zip(chunks, vectors):
                insert_chunk(
                    conn,
                    relpath,
                    chunk.index,
                    scope,
                    agent,
                    chunk.heading,
                    chunk.text,
                    vec,
                )
            set_file_hash(conn, relpath, digest, scope, agent)
            if verbose:
                print(f"indexed {relpath}  ({len(chunks)} chunks)")

        conn.commit()
        if verbose:
            print("reconcile done.")
    finally:
        conn.close()
