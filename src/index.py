"""One-way sync: .md -> vector index, scoped to a project root.

Walk the project's memory tree, hash-diff against the DB, reindex only
changed files, prune anything that disappeared. Idempotent and
deterministic. The .md files are the single source of truth.

The DB is created lazily: a session in a directory with no memory (and
no existing index) creates nothing. The sha256 diff is computed BEFORE
the model is touched, so a no-change SessionStart never loads the model.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .chunker import split_markdown
from .config import ProjectPaths, resolve
from .store import (
    connect,
    delete_file,
    get_indexed_hashes,
    insert_chunk,
    set_file_hash,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _discover(paths: ProjectPaths) -> list[tuple[Path, str, str | None]]:
    """Every memory .md as (path, scope, agent_name). Sorted = deterministic."""
    found: list[tuple[Path, str, str | None]] = []
    if paths.project_memory.exists():
        for p in sorted(paths.project_memory.rglob("*.md")):
            found.append((p, "project", None))
    if paths.agent_memory.exists():
        for agent_dir in sorted(d for d in paths.agent_memory.iterdir() if d.is_dir()):
            for p in sorted(agent_dir.rglob("*.md")):
                found.append((p, "agent", agent_dir.name))
    return found


def _disk(paths: ProjectPaths) -> dict[str, tuple[Path, str, str | None]]:
    return {
        str(p.relative_to(paths.root)): (p, scope, agent)
        for p, scope, agent in _discover(paths)
    }


def pending_embeddings(root: Path | str | None = None) -> int:
    """How many files would need (re)embedding — WITHOUT loading the model
    and WITHOUT creating the DB."""
    paths = resolve(root)
    disk = _disk(paths)
    if not paths.db.exists():
        return len(disk)  # nothing indexed yet -> all are new (0 if none)
    conn = connect(paths.db)
    try:
        indexed = get_indexed_hashes(conn)
    finally:
        conn.close()
    return sum(
        1 for rel, (p, _s, _a) in disk.items()
        if indexed.get(rel) != _sha256(p)
    )


def reindex(root: Path | str | None = None, verbose: bool = True) -> None:
    """Full reconcile for a project root: reindex changed, prune removed.

    Creates the DB only when there is real work (memory present) or an
    index already exists — never for an empty/unrelated directory.
    """
    paths = resolve(root)
    disk = _disk(paths)
    if not paths.db.exists() and not disk:
        if verbose:
            print(f"nothing to index [{paths.root}] (no memory, no DB)")
        return

    conn = connect(paths.db)  # only now is the DB file created
    try:
        indexed = get_indexed_hashes(conn)

        for gone in sorted(set(indexed) - set(disk)):
            delete_file(conn, gone)
            if verbose:
                print(f"pruned  {gone}")

        for relpath, (path, scope, agent) in disk.items():
            digest = _sha256(path)
            if indexed.get(relpath) == digest:
                continue
            delete_file(conn, relpath)
            chunks = split_markdown(path.read_text(encoding="utf-8"))
            texts = [c.text for c in chunks]
            if not texts:
                set_file_hash(conn, relpath, digest, scope, agent)
                continue
            # Embed via the warm resident so neither the PostToolUse hook
            # nor the long-lived MCP process loads the model itself.
            # Fall back in-process only if the resident is unreachable.
            from .embed_server import embed_passages_via_server
            vectors = embed_passages_via_server(texts)
            if vectors is None:
                from .embedder import embed_passages
                vectors = embed_passages(texts)
            for chunk, vec in zip(chunks, vectors):
                insert_chunk(conn, relpath, chunk.index, scope, agent,
                             chunk.heading, chunk.text, vec)
            set_file_hash(conn, relpath, digest, scope, agent)
            if verbose:
                print(f"indexed {relpath}  ({len(chunks)} chunks)")

        conn.commit()
        if verbose:
            print(f"reconcile done [{paths.root}] -> {paths.db.name}")
    finally:
        conn.close()
