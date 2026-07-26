"""One-way sync: .md -> vector index, for one bank root.

Walk the bank, hash-diff against the DB, reindex only changed files, prune
anything that disappeared. Idempotent and deterministic. The .md files are
the single source of truth.

v3 splits the work in two, because the halves have different costs and
different failure modes (Memory-contracts-v3 §4):

* **walk + diff** (``scan_bank`` → ``build_plan``) touches no model and
  writes nothing. A no-change run stops here, which is why an unchanged bank
  never loads the model;
* **embed + write** (``index_file``) works in batches of at most
  ``BATCH_SIZE`` chunks and **commits after every batch**, so no single unit
  of work can run long enough to hit a timeout, and a kill loses at most one
  batch of effort.

**Crash safety is file-granular** (§4.2, accepted deferral). The order per
file is: delete the old chunks *and* the ``files`` row and commit → embed and
commit each batch → write the hash and commit last. So a process killed
mid-file leaves chunks with no ``files`` row; the next run sees that file as
new, wipes the partial remains and redoes it whole, while every *completed*
file is skipped by hash and never re-embedded. Duplicates are impossible
because ``chunk_uid`` is deterministic and the file is cleared before insert.

The practical cost of that choice: a single very large file is the unit that
gets redone. A 200-chunk file killed at chunk 199 re-embeds all 200 next run.
Files are seconds, not hours, and true intra-file resume would mean keeping a
"batches done" counter consistent with the disk — a new class of bug for a
small win. Interrupting a *bank* is cheap; interrupting one huge file is not.
"""
from __future__ import annotations

import fnmatch
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .chunker import split_markdown
from .config import (
    BATCH_SIZE,
    DEFAULT_EXCLUDE,
    FOLD_PATH_CASE,
    BankPaths,
    resolve,
)
from .providers import EmbeddingProvider, EmbeddingUnavailable, get_provider
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


# ------------------------------------------------------------------ types


@dataclass(frozen=True)
class FileStat:
    """One markdown file on disk, already hashed."""

    path: str          # POSIX relpath from the bank root
    abs_path: Path
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class IndexPlan:
    """What a reconcile would do — computed without touching the model."""

    added: list[FileStat] = field(default_factory=list)
    changed: list[FileStat] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.changed or self.removed)

    @property
    def work(self) -> list[FileStat]:
        """Files needing embedding: added first, then changed."""
        return self.added + self.changed


@dataclass(frozen=True)
class BatchResult:
    """One committed batch — the unit of progress reporting."""

    path: str
    batch: int            # 0-based
    batches: int
    chunks: int           # chunks written by this batch


@dataclass(frozen=True)
class ReconcileResult:
    files_indexed: int
    chunks_indexed: int
    files_pruned: int
    took_ms: float
    errors: list[tuple[str, str]] = field(default_factory=list)


# ------------------------------------------------------------------- walk


def path_is_excluded(rel: str, patterns: list[str]) -> bool:
    """Does this POSIX relpath fall under one of the exclude globs?

    Two shapes are supported, which covers everything the registry's default
    list needs. ``NAME/**`` excludes that directory **at any depth** — the
    common case is ``__pycache__`` or a nested ``.venv``, and a pattern that
    only matched at the top level would quietly let those through. Anything
    else is a plain fnmatch against the whole relpath.

    Matching folds case where the filesystem does (``config.FOLD_PATH_CASE``):
    on Windows a folder called ``Venv`` is the same folder as ``venv``, and an
    exclude list that missed it would index a virtualenv on one machine and
    not on another.
    """
    if FOLD_PATH_CASE:
        rel = rel.lower()
        patterns = [p.lower() for p in patterns]
    segments = rel.split("/")
    for pattern in patterns:
        if pattern.endswith("/**"):
            if pattern[:-3] in segments[:-1]:
                return True
        elif fnmatch.fnmatchcase(rel, pattern):
            return True
    return False


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scan_bank(
    root: Path, *, exclude: list[str] | None = None
) -> dict[str, FileStat]:
    """Walk ``*.md`` under root and hash each. No DB, no model.

    Sorted, POSIX relpaths, deterministic. An unreadable file is skipped
    rather than fatal: one bad file must not stop a bank from indexing.
    """
    patterns = DEFAULT_EXCLUDE if exclude is None else exclude
    if not root.is_dir():
        return {}
    found: dict[str, FileStat] = {}
    for abs_path in sorted(root.rglob("*.md")):
        if not abs_path.is_file():
            continue
        rel = abs_path.relative_to(root).as_posix()
        if path_is_excluded(rel, patterns):
            continue
        try:
            stat = abs_path.stat()
            digest = _sha256(abs_path)
        except OSError:
            continue
        found[rel] = FileStat(
            path=rel,
            abs_path=abs_path,
            sha256=digest,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
    return found


def build_plan(conn, disk: dict[str, FileStat]) -> IndexPlan:
    """Pure hash-diff against the DB state. No DB writes."""
    indexed = get_indexed_hashes(conn)
    added: list[FileStat] = []
    changed: list[FileStat] = []
    for rel, fs in disk.items():
        known = indexed.get(rel)
        if known is None:
            added.append(fs)
        elif known != fs.sha256:
            changed.append(fs)
    removed = sorted(set(indexed) - set(disk))
    return IndexPlan(added=added, changed=changed, removed=removed)


# ------------------------------------------------------------ embed+write


def index_file(
    conn,
    provider: EmbeddingProvider,
    fs: FileStat,
    *,
    batch_size: int = BATCH_SIZE,
    start_batch: int = 0,
    on_batch: Callable[[BatchResult], None] | None = None,
    should_yield: Callable[[], bool] | None = None,
) -> int | None:
    """Index one file, committing after every batch.

    Returns the number of chunks written when the file is finished, or
    ``None`` when it did not finish — either it vanished mid-flight, or
    ``should_yield()`` asked us to step aside. In the second case the caller
    resumes from ``last BatchResult.batch + 1``: every committed batch is
    reported through ``on_batch``, so the resume point is already known and
    does not need a second, ambiguous meaning on the return value.

    ``start_batch > 0`` resumes a preempted file: the wipe in step 1 is not
    repeated, but the file's hash is re-checked first — if it changed while
    the task waited, resuming would splice two versions together, so we
    restart it from scratch instead.
    """
    try:
        text = fs.abs_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None  # vanished or unreadable -> the next scan decides

    if start_batch > 0 and _sha256(fs.abs_path) != fs.sha256:
        start_batch = 0  # changed under us: the partial work is not reusable

    chunks = split_markdown(text)
    batches = max(1, (len(chunks) + batch_size - 1) // batch_size)

    if start_batch == 0:
        # Step 1: the file becomes "not indexed" atomically. Everything that
        # follows is additive, so a crash can only leave orphan chunks that
        # the next run wipes — never a stale hash claiming to be current.
        delete_file(conn, fs.path)
        conn.commit()

    if not chunks:
        set_file_hash(
            conn, path=fs.path, sha256=fs.sha256, size=fs.size,
            mtime_ns=fs.mtime_ns, n_chunks=0,
        )
        conn.commit()
        return 0

    written = 0
    for batch_no in range(start_batch, batches):
        window = chunks[batch_no * batch_size:(batch_no + 1) * batch_size]
        if not window:
            continue
        # Step 2: one batch = one embed call + N inserts + ONE commit.
        vectors = provider.embed_passages([c.text for c in window])
        for chunk, vec in zip(window, vectors):
            insert_chunk(
                conn,
                chunk_uid=chunk_uid(fs.path, chunk.index),
                path=fs.path,
                chunk_index=chunk.index,
                heading=chunk.heading,
                content=chunk.text,
                start_char=chunk.start,
                end_char=chunk.end,
                embedding=vec,
            )
        conn.commit()
        written += len(window)
        if on_batch is not None:
            on_batch(BatchResult(fs.path, batch_no, batches, len(window)))
        if (
            batch_no + 1 < batches
            and should_yield is not None
            and should_yield()
        ):
            return None  # preempted between batches; resume from batch_no + 1

    # Step 3: the hash goes in last, so it can never claim more than is stored.
    set_file_hash(
        conn, path=fs.path, sha256=fs.sha256, size=fs.size,
        mtime_ns=fs.mtime_ns, n_chunks=len(chunks),
    )
    conn.commit()
    return written


def prune(conn, removed: list[str]) -> int:
    """Drop files that are gone from disk, with all their chunks."""
    for rel in removed:
        delete_file(conn, rel)
    if removed:
        conn.commit()
    return len(removed)


def reconcile(
    conn,
    provider: EmbeddingProvider,
    root: Path,
    *,
    exclude: list[str] | None = None,
    batch_size: int = BATCH_SIZE,
    on_batch: Callable[[BatchResult], None] | None = None,
) -> ReconcileResult:
    """Full walk + diff + index changed + prune removed.

    Used by the bulk path and by reconcile-on-start. A per-file failure is
    recorded and the walk continues; ``EmbeddingUnavailable`` is NOT caught,
    because it means nothing can be embedded right now and retrying 200 files
    would just be 200 identical failures. Whatever committed before it stays
    committed and the next run resumes by hash — which is the soft
    degradation NFR-10 asks for.
    """
    started = time.perf_counter()
    disk = scan_bank(root, exclude=exclude)
    plan = build_plan(conn, disk)

    pruned = prune(conn, plan.removed)
    files_indexed = chunks_indexed = 0
    errors: list[tuple[str, str]] = []

    for fs in plan.work:
        try:
            written = index_file(
                conn, provider, fs,
                batch_size=batch_size, on_batch=on_batch,
            )
        except EmbeddingUnavailable:
            raise
        except Exception as exc:  # one bad file must not stop the bank
            errors.append((fs.path, f"{type(exc).__name__}: {exc}"))
            continue
        if written is None:
            continue
        files_indexed += 1
        chunks_indexed += written

    mark_indexed(conn)
    conn.commit()
    return ReconcileResult(
        files_indexed=files_indexed,
        chunks_indexed=chunks_indexed,
        files_pruned=pruned,
        took_ms=(time.perf_counter() - started) * 1000.0,
        errors=errors,
    )


# --------------------------------------------------------- bank-level API


def _disk(paths: BankPaths) -> dict[str, Path]:
    """Every indexable .md in the bank as POSIX relpath -> absolute path.

    The path-only view of ``scan_bank``, for callers that just need to know
    what is there (and for the platform test that pins relpath
    normalisation). Anything that goes on to index wants ``scan_bank``, whose
    ``FileStat`` already carries the hash and stat the plan needs.
    """
    return {rel: fs.abs_path for rel, fs in scan_bank(paths.root).items()}


def _open_bank(paths: BankPaths, provider: EmbeddingProvider, verbose: bool):
    """Open the bank DB and bind it to the active provider.

    A provider/model/dim change wipes the content here, so the reconcile that
    follows re-embeds everything from the .md.
    """
    conn = connect(paths.db, dim=provider.dim)
    if needs_rebuild(conn, provider_key=provider.key, dim=provider.dim):
        # Wipe BEFORE claiming the new identity. If meta were written first
        # and we died here, it would advertise the new provider over the old
        # provider's vectors, `needs_rebuild` would answer False forever, and
        # search would blend incomparable vectors with nothing to notice it.
        reset_index(conn, dim=provider.dim)
        if verbose:
            print(f"provider changed -> full rebuild of {paths.db.name}")
    init_meta(
        conn,
        bank_id=paths.id,
        bank_root=paths.root.as_posix(),
        provider_key=provider.key,
        dim=provider.dim,
    )
    return conn


def pending_embeddings(root: Path | str | None = None) -> int:
    """How many files would need (re)embedding — WITHOUT loading the model
    and WITHOUT creating the DB."""
    paths = resolve(root)
    disk = scan_bank(paths.root)  # FileStat, not just paths: the plan needs hashes
    if not paths.db.exists():
        return len(disk)  # nothing indexed yet -> all are new (0 if none)
    conn = connect(paths.db)
    try:
        plan = build_plan(conn, disk)
    finally:
        conn.close()
    return len(plan.work)


def reindex(root: Path | str | None = None, verbose: bool = True) -> None:
    """Full reconcile for a bank root: reindex changed, prune removed.

    The bank-level entry point behind `mnemo ingest` and the PostToolUse
    hook. Creates the DB only when there is real work (markdown present) or
    an index already exists — never for an empty/unrelated directory.
    """
    paths = resolve(root)
    if not paths.db.exists() and not _disk(paths):
        if verbose:
            print(f"nothing to index [{paths.root}] (no .md, no DB)")
        return

    provider = get_provider()  # a handle only — the model is not loaded here
    conn = _open_bank(paths, provider, verbose)
    try:
        def announce(batch: BatchResult) -> None:
            if batch.batches > 1:
                print(f"  {batch.path}  batch {batch.batch + 1}/{batch.batches}")

        result = reconcile(
            conn, provider, paths.root,
            on_batch=announce if verbose else None,
        )
        if verbose:
            for rel, message in result.errors:
                print(f"failed  {rel}: {message}")
            print(
                f"reconcile done [{paths.root}] -> {paths.db.name}  "
                f"indexed={result.files_indexed} chunks={result.chunks_indexed} "
                f"pruned={result.files_pruned} in {result.took_ms / 1000:.1f}s"
            )
    finally:
        conn.close()
