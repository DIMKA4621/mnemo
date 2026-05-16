"""CLI entry points: ingest (.md -> index) and search (query -> sections)."""
from __future__ import annotations

import argparse

from .config import TOP_K
from .index import reindex
from .search import search


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="memory-poc",
        description="Memory POC: .md -> chunk -> embed -> sqlite-vec -> search.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "ingest",
        help="Reconcile .md -> vector index (hash-diff + prune).",
    )

    s = sub.add_parser("search", help="Semantic search over project memory.")
    s.add_argument("query")
    s.add_argument("--scope", choices=["project", "agent"])
    s.add_argument("--agent", help="Filter to a single agent's memory.")
    s.add_argument("-k", "--top-k", type=int, default=TOP_K)

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        reindex()
        return

    hits = search(
        args.query,
        scope=args.scope,
        agent_name=args.agent,
        top_k=args.top_k,
    )
    if not hits:
        print("No relevant results.")
        return
    for i, h in enumerate(hits, 1):
        tag = h.scope + (f"/{h.agent_name}" if h.agent_name else "")
        print(
            f"\n[{i}] {h.path}  ·  {h.heading or '(no heading)'}  ·  "
            f"{tag}  ·  score={h.score:.4f}"
        )
        snippet = " ".join(h.content.split())
        print(f"    {snippet[:300]}{'…' if len(snippet) > 300 else ''}")


if __name__ == "__main__":
    main()
