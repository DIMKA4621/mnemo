"""One-way sync: .md -> vector index, for one bank root.

Walk the bank, hash-diff against the DB, reindex only changed files, prune
anything that disappeared. Idempotent and deterministic. The .md files are
the single source of truth.

v3: a bank is flat — every ``.md`` anywhere under the root belongs to the
same index, with no project/agent split. Separation is achieved by
registering separate banks, not by scopes inside one.

The DB is created lazily: a session in a directory with no markdown (and no
existing index) creates nothing. The sha256 diff is computed BEFORE the model
is touched, so a no-change run never loads it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .chunker import split_markdown
from .config import BankPaths, resolve
from .providers import EmbeddingUnavailable, get_provider
from .store import (
    chunk_uid,
    connect,
    delete_file,
    get_indexed_hashes,
    init_meta,
    insert_chunk,
    mark_indexed,
    needs_rebuild,
    reset_index,
    set_file_hash,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _disk(paths: BankPaths) -> dict[str, Path]:
    """Every .md in the bank as POSIX relpath -> path. Sorted = deterministic."""
    if not paths.root.is_dir():
        return {}
    return {
        p.relative_to(paths.root).as_posix(): p
        for p in sorted(paths.root.rglob("*.md"))
        if p.is_file()
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
    return sum(1 for rel, p in disk.items() if indexed.get(rel) != _sha256(p))


def reindex(root: Path | str | None = None, verbose: bool = True) -> None:
    """Full reconcile for a bank root: reindex changed, prune removed.

    Creates the DB only when there is real work (markdown present) or an
    index already exists — never for an empty/unrelated directory.
    """
    paths = resolve(root)
    disk = _disk(paths)
    if not paths.db.exists() and not disk:
        if verbose:
            print(f"nothing to index [{paths.root}] (no .md, no DB)")
        return

    provider = get_provider()  # a handle only — the model is not loaded here
    conn = connect(paths.db)  # only now is the DB file created
    try:
        rebuild = needs_rebuild(
            conn, provider_key=provider.key, dim=provider.dim
        )
        init_meta(
            conn,
            bank_id=paths.id,
            bank_root=paths.root.as_posix(),
            provider_key=provider.key,
            dim=provider.dim,
        )
        if rebuild:
            # A different provider/model produced the stored vectors; they are
            # not comparable to new ones, so the bank is rebuilt from the .md.
            reset_index(conn)
            if verbose:
                print(f"provider changed -> full rebuild of {paths.db.name}")

        indexed = get_indexed_hashes(conn)

        for gone in sorted(set(indexed) - set(disk)):
            delete_file(conn, gone)
            if verbose:
                print(f"pruned  {gone}")

        for relpath, path in disk.items():
            digest = _sha256(path)
            if indexed.get(relpath) == digest:
                continue
            delete_file(conn, relpath)
            stat = path.stat()
            chunks = split_markdown(path.read_text(encoding="utf-8"))
            texts = [c.text for c in chunks]
            if texts:
                # Vectors come from the provider: the local one prefers the
                # warm resident, so no hook and no MCP process loads the model.
                try:
                    vectors = provider.embed_passages(texts)
                except EmbeddingUnavailable as exc:
                    raise EmbeddingUnavailable(
                        f"cannot embed {relpath}: {exc}"
                    ) from exc
                for chunk, vec in zip(chunks, vectors):
                    insert_chunk(
                        conn,
                        chunk_uid=chunk_uid(relpath, chunk.index),
                        path=relpath,
                        chunk_index=chunk.index,
                        heading=chunk.heading,
                        content=chunk.text,
                        start_char=chunk.start,
                        end_char=chunk.end,
                        embedding=vec,
                    )
            set_file_hash(
                conn,
                path=relpath,
                sha256=digest,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                n_chunks=len(chunks),
            )
            if verbose and chunks:
                print(f"indexed {relpath}  ({len(chunks)} chunks)")

        mark_indexed(conn)
        conn.commit()
        if verbose:
            print(f"reconcile done [{paths.root}] -> {paths.db.name}")
    finally:
        conn.close()
