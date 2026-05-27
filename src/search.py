"""Read path: query -> relevant sections, scoped to a project root.

Vector search is primary; FTS5/BM25 is the secondary lexical net.
Results are blended with reciprocal rank fusion (RRF). Scope
(project / agent) is filtered on chunk metadata.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import sqlite_vec

from .config import MIN_QUERY_CHARS, MIN_SIM, NEIGHBOR_WINDOW, RRF_K, TOP_K, resolve
from .embedder import embed_query
from .store import connect, get_vectors


@dataclass
class Hit:
    path: str
    heading: str
    scope: str
    agent_name: str | None
    content: str
    score: float
    sim: float | None = None  # cosine sim vs query; set only on gated calls
    chunk_index: int = -1     # position in file; -1 only for merged windows
    span: tuple[int, int] | None = None  # (first, last) chunk_index after merge


def _vector_ranked(conn: sqlite3.Connection, qvec: list[float], limit: int) -> list[int]:
    rows = conn.execute(
        "SELECT rowid FROM vec_chunks "
        "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (sqlite_vec.serialize_float32(qvec), limit),
    ).fetchall()
    return [r["rowid"] for r in rows]


def _fts_escape(query: str) -> str:
    """Treat the whole query as one quoted phrase — robust against operators."""
    return '"' + query.replace('"', '""') + '"'


def _fts_ranked(conn: sqlite3.Connection, query: str, limit: int) -> list[int]:
    rows = conn.execute(
        "SELECT rowid FROM fts_chunks WHERE fts_chunks MATCH ? "
        "ORDER BY bm25(fts_chunks) LIMIT ?",
        (_fts_escape(query), limit),
    ).fetchall()
    return [r["rowid"] for r in rows]


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
                    path=path,
                    heading=best.heading,
                    scope=best.scope,
                    agent_name=best.agent_name,
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
    query: str,
    *,
    root: Path | str | None = None,
    scope: str | None = None,
    agent_name: str | None = None,
    top_k: int = TOP_K,
    qvec: list[float] | None = None,
    gate: bool = False,
    min_sim: float | None = None,
    expand_window: int | None = None,
) -> list[Hit]:
    """Hybrid (vector-primary + FTS) search with optional scope filter.

    ``qvec``: a precomputed query embedding (the auto-inject path passes the
    one obtained from the warm helper, so the model is never loaded in this
    process). ``gate``: drop empty/too-short queries and weak matches by a
    cosine-similarity floor — used ONLY by auto-inject; manual MCP/CLI
    search stays ungated (the agent judges relevance itself).
    """
    if gate and len(query.strip()) < MIN_QUERY_CHARS:
        return []
    db = resolve(root).db
    if not db.exists():
        return []
    conn = connect(db)
    try:
        if qvec is None:
            qvec = embed_query(query)
        pool = max(top_k * 4, 20)
        vec_ids = _vector_ranked(conn, qvec, pool)
        try:
            fts_ids = _fts_ranked(conn, query, pool)
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
            if scope and row["scope"] != scope:
                continue
            if agent_name and row["agent_name"] != agent_name:
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
                    path=row["path"],
                    heading=row["heading"] or "",
                    scope=row["scope"],
                    agent_name=row["agent_name"],
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
    finally:
        conn.close()
