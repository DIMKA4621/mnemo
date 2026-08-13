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

from .chunker import Chunk, split_markdown
from .config import (
    BATCH_SIZE,
    DEFAULT_EXCLUDE,
    FOLD_PATH_CASE,
    BankPaths,
    resolve,
)
from .providers import EmbeddingProvider, EmbeddingUnavailable, get_provider
from .providers.base import DEFAULT_PAD_BUDGET
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
    # Cumulative within the file, and both exact from the first batch on.
    # They are reported rather than derived because batches are no longer
    # uniform: `plan_batches` sizes each one by padded cost, so neither
    # `batch * BATCH_SIZE` nor `batches * BATCH_SIZE` means anything now.
    # `index_file` holds the plan and can simply count, including across a
    # resume, where a caller has nothing to count from.
    chunks_done: int = 0
    chunks_total: int = 0


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


def plan_batches(
    chunks: list[Chunk],
    *,
    batch_size: int = BATCH_SIZE,
    budget: int | None = None,
) -> list[list[Chunk]]:
    """Group a file's chunks into embed calls, cheapest-padding first.

    A batch is padded to its longest member, so it costs ``longest x count``
    and not ``count``. Measured, that padded total is the *only* thing the
    wall clock tracks (seconds = padded_tokens / 243, within 1.4% across four
    strategies) — which turns "what batch size is best" from an experiment
    into arithmetic.

    Two rules, in this order:

    1. **Sort by length.** This is the whole win: +36% pooled, +14-18% per
       file. Mixing a 60-char chunk with a 1200-char one pads the short one
       twentyfold; neighbours of a similar length pad each other barely at
       all. Natural document order measured *worse* than a deliberate
       shuffle, so the behaviour this replaces was close to the worst case.
    2. **Cut on the padded budget**, ``BATCH_SIZE`` as a backstop in items.
       Packing on its own is not merely neutral, it is harmful: pooling
       without sorting produced fewer batches and a *longer* run (0.92x).
       The budget exists to bound cost, the sort to lower it.

    Per file, never across files. Cross-file batching measures better still
    (1.85x vs 1.63x) and is not available to us: a batch is the commit unit,
    the preemption point, the eviction-recovery unit and the error-isolation
    boundary, and all four are defined per file (module docstring, and
    Memory-contracts-v3 §4.1).

    ``budget`` defaults to the **conservative** shared value rather than to
    the CPU's measured one, because callers that do not pass it also do not
    know which backend is on the other end — and the wrong value is silent.
    ``index_file`` always passes ``provider.pad_budget``.

    Deterministic — same chunks in, same grouping out — because ``start_batch``
    indexes into this list across a preemption, and a resume that regrouped
    differently would skip or redo chunks.
    """
    if not chunks:
        return []
    if budget is None:
        budget = DEFAULT_PAD_BUDGET
    # `index` breaks ties so equal-length chunks keep document order: a stable
    # key is what makes the grouping reproducible for a resume.
    ordered = sorted(chunks, key=lambda c: (len(c.text), c.index))
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    for chunk in ordered:
        size = len(chunk.text)
        # Ascending order means the candidate always IS the new longest, so
        # the batch's padded cost is exactly `size * (len(current) + 1)`.
        # A single chunk over budget still gets in — as its own batch, since
        # `current` is empty — because dropping it is not an option.
        if current and (
            len(current) >= batch_size or size * (len(current) + 1) > budget
        ):
            batches.append(current)
            current = []
        current.append(chunk)
    if current:
        batches.append(current)
    return batches


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
    # The budget comes from the provider: the CPU resident and a GPU endpoint
    # want opposite values, and the wrong one is 2x slower with nothing to
    # notice it by (`EmbeddingProvider.pad_budget`).
    plan = plan_batches(chunks, batch_size=batch_size,
                        budget=provider.pad_budget)
    batches = max(1, len(plan))

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
    # Chunks in the batches we are skipping — exact, because the plan is
    # deterministic and this is the same grouping the earlier run used.
    done = sum(len(b) for b in plan[:start_batch])
    for batch_no in range(start_batch, batches):
        window = plan[batch_no] if batch_no < len(plan) else []
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
        done += len(window)
        if on_batch is not None:
            on_batch(BatchResult(fs.path, batch_no, batches, len(window),
                                 chunks_done=done, chunks_total=len(chunks)))
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
            print(f"provider or chunking changed -> full rebuild of "
                  f"{paths.db.name}")
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
    if not paths.db.exists() and not scan_bank(paths.root):
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
