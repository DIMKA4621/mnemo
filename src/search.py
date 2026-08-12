"""Read path: query -> relevant sections of one bank.

Vector search is primary; FTS5/BM25 is the secondary lexical net. Results are
blended with reciprocal rank fusion (RRF).

v3: the bank is flat, so there is no scope filter. Narrowing is optional and
purely navigational — ``path_prefix`` matches on the ``chunks.path`` that is
already stored, at segment boundaries, so no new metadata is needed.

This module stays **pure** (Memory-contracts-v3 §5): it is handed an open
connection and a provider, and knows nothing about banks, the registry or the
queue. It never composes a status — ``indexing`` / ``empty`` / ``ready``
depends on the work queue, which lives in the API layer.
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass

import sqlite_vec

from .config import (
    FOLD_PATH_CASE,
    MIN_QUERY_CHARS,
    MIN_SIM,
    NEIGHBOR_WINDOW,
    RRF_K,
    TOP_K,
)
from .providers import EmbeddingProvider, EmbeddingUnavailable, get_provider
from .store import get_vectors

@dataclass
class Hit:
    """One result. For a merged neighbour window (``span`` set), ``chunk_uid``
    and ``chunk_index`` identify the *anchor* — the best-scoring chunk in the
    window — while ``span`` gives the inclusive range of chunk indices whose
    text was concatenated into ``content``. A UI highlighting a hit should
    follow ``span`` when present and ``chunk_uid`` only otherwise."""

    chunk_uid: str
    path: str                 # POSIX relpath inside the bank
    heading: str
    content: str
    score: float              # RRF score
    chunk_index: int = -1     # position in file; -1 only for merged windows
    span: tuple[int, int] | None = None  # (first, last) chunk_index after merge
    sim: float | None = None  # cosine sim vs query; set only on gated calls


def _normalize_prefix(path_prefix: str | None) -> str | None:
    """POSIX relpath from the bank root; '' and '.' mean "no filter".

    Each segment is stripped separately: a prefix pasted out of a UI arrives
    as ``" logs / 2026 "`` often enough, and stripping the whole string first
    would leave ``"logs / 2026"`` — which matches nothing, silently.
    """
    if not path_prefix:
        return None
    segments = [s.strip() for s in path_prefix.replace("\\", "/").split("/")]
    cleaned = "/".join(s for s in segments if s and s != ".")
    return cleaned or None


def _fold(value: str) -> str:
    """Case-fold where the filesystem does — NTFS indexes ``NOTES.MD`` and a
    user typing ``--path-prefix notes`` means it. POSIX keeps them distinct."""
    return value.lower() if FOLD_PATH_CASE else value


def _under_prefix(path: str, prefix: str) -> bool:
    """Segment-boundary match: 'log' must NOT match 'logs/x.md'."""
    path, prefix = _fold(path), _fold(prefix)
    return path == prefix or path.startswith(prefix + "/")


def _vector_ranked(conn: sqlite3.Connection, qvec: list[float], limit: int) -> list[int]:
    rows = conn.execute(
        "SELECT rowid FROM vec_chunks "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (sqlite_vec.serialize_float32(qvec), limit),
    ).fetchall()
    return [r["rowid"] for r in rows]


# A query term, for the lexical leg. `\w+` keeps letters, digits and `_` in
# any script and drops everything FTS5 would read as syntax (`*`, `^`, `:`,
# parentheses, `-`), which is what makes the result injection-proof without
# quoting the whole thing.
_FTS_TERM = re.compile(r"\w+", re.UNICODE)


def _fts_query(query: str) -> str | None:
    """Build the MATCH expression: OR over the query's terms.

    This used to wrap the whole query in quotes, and a quoted string in FTS5
    is a PHRASE — the words had to appear consecutively, in order. On a real
    question that matches nothing: measured on this repo's own bank,
    "чому індексація повільна" and "watcher debounce" both returned zero
    rows, while the same terms OR-ed returned 12 and 6. So RRF was fusing a
    full vector ranking with an empty list, and the hybrid search was
    vector-only for anything longer than a single word.

    The intent behind the quoting was sound — a user's question must never be
    read as FTS5 syntax — so each term is still quoted individually. A quoted
    term is a literal, so a stray ``AND`` / ``NEAR`` / ``*`` in the question
    cannot become an operator.

    OR rather than AND because this is a CANDIDATE POOL for RRF, not an
    answer: bm25() does the ranking, and a term that matches everything
    (a stopword) earns a low IDF and contributes almost nothing to the score.
    AND would reproduce the old failure — measured, it also returned zero on
    the same questions.

    Prefix matching (``term*``, for Ukrainian's suffix inflection) was tried
    and is not worth it here: 18 rows against 17 on the query where it helped
    most. Real stemming is not available — FTS5 ships `porter`, which is
    English-only.

    Returns None when the query holds no usable term, so the caller can skip
    the lexical leg instead of building a malformed expression.
    """
    terms = [t for t in _FTS_TERM.findall(query) if len(t) > 1]
    if not terms:
        return None
    # `\w+` cannot contain a double quote, so the terms need no escaping.
    return " OR ".join(f'"{t}"' for t in terms)


def _fts_ranked(
    conn: sqlite3.Connection, query: str, limit: int, prefix: str | None
) -> list[int]:
    match = _fts_query(query)
    if match is None:
        return []
    sql = "SELECT rowid FROM fts_chunks WHERE fts_chunks MATCH ?"
    params: list[object] = [match]
    if prefix is not None:
        # The lexical leg can narrow in SQL (unlike kNN), so it does. LIKE is
        # ASCII-case-insensitive in SQLite, which may over-select — harmless,
        # because `_under_prefix` is the authority and re-checks every row.
        # Under-selecting is what would silently lose hits, so never use `=`.
        sql += (
            " AND rowid IN (SELECT id FROM chunks "
            "WHERE path LIKE ? OR path LIKE ?)"
        )
        params += [prefix, prefix + "/%"]
    sql += " ORDER BY bm25(fts_chunks) LIMIT ?"
    params.append(limit)
    return [r["rowid"] for r in conn.execute(sql, params).fetchall()]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _rrf(*rankings: list[int]) -> dict[int, float]:
    """Reciprocal rank fusion: sum 1 / (RRF_K + rank)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def _expand_neighbors(
    conn: sqlite3.Connection, hits: list[Hit], window: int
) -> list[Hit]:
    """Widen each hit with ±window adjacent chunks from the same file and
    merge overlapping windows into one block. Original ranking is
    preserved: each merged block inherits the position of the best
    original hit it contains; heading/score/sim also come from that hit.
    Content is concatenated in chunk-order with a blank line between
    consecutive chunks (already so in the source markdown).
    """
    if not hits or window <= 0:
        return hits

    # 1) Per-path: collect (chunk_index, original_hit) pairs in original order.
    by_path: dict[str, list[Hit]] = {}
    path_order: list[str] = []
    for h in hits:
        if h.path not in by_path:
            path_order.append(h.path)
            by_path[h.path] = []
        by_path[h.path].append(h)

    expanded: list[Hit] = []
    for path in path_order:
        path_hits = by_path[path]
        # 2) Build windows and merge by sweeping over sorted indices.
        windows = sorted(
            (max(0, h.chunk_index - window), h.chunk_index + window, h)
            for h in path_hits
        )
        intervals: list[tuple[int, int, list[Hit]]] = []
        for lo, hi, h in windows:
            if intervals and lo <= intervals[-1][1] + 1:
                p_lo, p_hi, p_hits = intervals[-1]
                intervals[-1] = (p_lo, max(p_hi, hi), p_hits + [h])
            else:
                intervals.append((lo, hi, [h]))

        # 3) Fetch all chunks for each interval in one query per path.
        rows = conn.execute(
            "SELECT chunk_index, content, heading FROM chunks "
            "WHERE path = ? AND chunk_index BETWEEN ? AND ? "
            "ORDER BY chunk_index",
            (path, intervals[0][0], intervals[-1][1]),
        ).fetchall()
        by_idx = {r["chunk_index"]: r for r in rows}

        for lo, hi, members in intervals:
            # Best original hit in this interval drives heading/score/sim/order.
            best = max(members, key=lambda h: h.score)
            ordered_idxs = sorted(
                i for i in range(lo, hi + 1) if i in by_idx
            )
            if not ordered_idxs:
                continue
            content = "\n\n".join(by_idx[i]["content"] for i in ordered_idxs)
            expanded.append(
                Hit(
                    chunk_uid=best.chunk_uid,
                    path=path,
                    heading=best.heading,
                    content=content,
                    score=best.score,
                    sim=best.sim,
                    chunk_index=best.chunk_index,
                    span=(ordered_idxs[0], ordered_idxs[-1]),
                )
            )

    # Restore the global ranking order: sort merged blocks by best-member score.
    expanded.sort(key=lambda h: h.score, reverse=True)
    return expanded


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    qvec: list[float] | None = None,
    provider: EmbeddingProvider | None = None,
    top_k: int = TOP_K,
    path_prefix: str | None = None,
    gate: bool = False,
    min_sim: float | None = None,
    expand_window: int | None = None,
) -> list[Hit]:
    """Hybrid (vector-primary + FTS) search with an optional path filter.

    Takes an **open connection**: the caller owns the bank's lifecycle, which
    is what lets the backend hold one long-lived read connection per bank
    instead of reopening the database on every request.

    ``qvec``: a precomputed query embedding (the auto-inject path passes the
    one obtained from the warm helper, so the model is never loaded in this
    process). ``provider``: used only when ``qvec`` is None; defaults to the
    service provider. ``gate``: drop empty/too-short queries and weak matches
    by a cosine-similarity floor — used ONLY by auto-inject; manual MCP/CLI
    search stays ungated (the agent judges relevance itself).
    """
    if gate and len(query.strip()) < MIN_QUERY_CHARS:
        return []
    prefix = _normalize_prefix(path_prefix)
    try:
        if qvec is None:
            # A search must never trigger an implicit ~2 GB download; if it
            # cannot embed, it degrades to "no results" (NFR-10).
            try:
                qvec = (provider or get_provider()).embed_query(query)
            except EmbeddingUnavailable:
                return []
        # kNN cannot filter by path, so the filter is applied after it. With a
        # prefix the candidate pool is widened, otherwise a narrow subfolder in
        # a big bank returns almost nothing.
        pool = (
            min(max(top_k * 40, 200), 500)
            if prefix is not None
            else max(top_k * 4, 20)
        )
        vec_ids = _vector_ranked(conn, qvec, pool)
        try:
            fts_ids = _fts_ranked(conn, query, pool, prefix)
        except sqlite3.OperationalError:
            fts_ids = []
        fused = _rrf(vec_ids, fts_ids)
        if not fused:
            return []

        ranked = sorted(fused, key=lambda c: fused[c], reverse=True)
        placeholders = ",".join("?" * len(ranked))
        meta = {
            r["id"]: r
            for r in conn.execute(
                f"SELECT * FROM chunks WHERE id IN ({placeholders})", ranked
            )
        }

        gate_min = (MIN_SIM if min_sim is None else min_sim) if gate else 0.0
        gate_vecs = get_vectors(conn, ranked) if gate else {}

        hits: list[Hit] = []
        for cid in ranked:
            row = meta.get(cid)
            if row is None:
                continue
            if prefix is not None and not _under_prefix(row["path"], prefix):
                continue
            sim: float | None = None
            if gate:
                cv = gate_vecs.get(cid)
                if cv is None:
                    continue
                sim = _cosine(qvec, cv)
                if sim < gate_min:
                    continue
            hits.append(
                Hit(
                    chunk_uid=row["chunk_uid"],
                    path=row["path"],
                    heading=row["heading"] or "",
                    content=row["content"],
                    score=fused[cid],
                    sim=sim,
                    chunk_index=row["chunk_index"],
                )
            )
            if len(hits) >= top_k:
                break
        win = NEIGHBOR_WINDOW if expand_window is None else expand_window
        return _expand_neighbors(conn, hits, win)
    except sqlite3.OperationalError as exc:
        # A bank whose schema was never created reads as "nothing here" — the
        # connection may deliberately be read-only, so we do not create it.
        if "no such table" in str(exc):
            return []
        raise
