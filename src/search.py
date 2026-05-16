"""Read path: query -> relevant sections.

Vector search is primary; FTS5/BM25 is the secondary lexical safety net.
Results are blended with reciprocal rank fusion (RRF). Scope (project /
agent) is filtered on the chunk metadata.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import sqlite_vec

from .config import RRF_K, TOP_K
from .embedder import embed_query
from .store import connect


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
        """
        SELECT rowid
        FROM vec_chunks
        WHERE embedding MATCH ? AND k = ?
        ORDER BY distance
        """,
        (sqlite_vec.serialize_float32(qvec), limit),
    ).fetchall()
    return [r["rowid"] for r in rows]


def _fts_escape(query: str) -> str:
    """Treat the whole query as one quoted phrase — robust against operators."""
    return '"' + query.replace('"', '""') + '"'


def _fts_ranked(conn: sqlite3.Connection, query: str, limit: int) -> list[int]:
    rows = conn.execute(
        """
        SELECT rowid
        FROM fts_chunks
        WHERE fts_chunks MATCH ?
        ORDER BY bm25(fts_chunks)
        LIMIT ?
        """,
        (_fts_escape(query), limit),
    ).fetchall()
    return [r["rowid"] for r in rows]


def _rrf(*rankings: list[int]) -> dict[int, float]:
    """Reciprocal rank fusion: sum 1 / (RRF_K + rank)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    return scores


def search(
    query: str,
    scope: str | None = None,
    agent_name: str | None = None,
    top_k: int = TOP_K,
) -> list[Hit]:
    """Hybrid (vector-primary + FTS) search with optional scope filter."""
    conn = connect()
    try:
        pool = max(top_k * 4, 20)
        vec_ids = _vector_ranked(conn, embed_query(query), pool)
        try:
            fts_ids = _fts_ranked(conn, query, pool)
        except sqlite3.OperationalError:
            fts_ids = []  # FTS is only a safety net; never fail the search on it
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

        hits: list[Hit] = []
        for cid in ranked:
            row = meta.get(cid)
            if row is None:
                continue
            if scope and row["scope"] != scope:
                continue
            if agent_name and row["agent_name"] != agent_name:
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
