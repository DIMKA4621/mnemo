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

from .config import MIN_QUERY_CHARS, MIN_SIM, RRF_K, TOP_K, resolve
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
            if gate:
                cv = gate_vecs.get(cid)
                if cv is None or _cosine(qvec, cv) < gate_min:
                    continue
            hits.append(
                Hit(
                    path=row["path"],
                    heading=row["heading"] or "",
                    scope=row["scope"],
                    agent_name=row["agent_name"],
                    content=row["content"],
                    score=fused[cid],
                )
            )
            if len(hits) >= top_k:
                break
        return hits
    finally:
        conn.close()
