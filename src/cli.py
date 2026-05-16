"""CLI entry points — the single engine behind hooks and (later) MCP.

  warmup            explicit one-time 2 GB model download + sanity check
  ingest [--root]   reconcile a project's .md -> its index (hook target)
  search [--root]   semantic search over a project's memory

`--root` defaults to the current directory, so the SessionStart hook
indexes whatever project the session is in.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import TOP_K, resolve
from .embedder import is_model_cached, warmup
from .index import pending_embeddings, reindex
from .search import search


def _cmd_warmup() -> int:
    print(f"Downloading / loading model (one-time, ~2.2 GB) ...")
    dim = warmup()
    print(f"READY — model cached, test embedding dim = {dim}")
    return 0


def _cmd_ingest(root: Path) -> int:
    # Never silently pull 2 GB inside a hook: refuse if work needs the
    # model but it was never warmed up.
    if pending_embeddings(root) and not is_model_cached():
        print(
            "memory-poc: changes detected but the model is not cached.\n"
            "Run `mem-index warmup` once, then this will work.",
            file=sys.stderr,
        )
        return 2
    reindex(root)
    return 0


def _cmd_search(args: argparse.Namespace) -> int:
    hits = search(
        args.query,
        root=args.root,
        scope=args.scope,
        agent_name=args.agent,
        top_k=args.top_k,
    )
    if not hits:
        print("No relevant results.")
        return 0
    for i, h in enumerate(hits, 1):
        tag = h.scope + (f"/{h.agent_name}" if h.agent_name else "")
        print(
            f"\n[{i}] {h.path}  ·  {h.heading or '(no heading)'}  ·  "
            f"{tag}  ·  score={h.score:.4f}"
        )
        snippet = " ".join(h.content.split())
        print(f"    {snippet[:300]}{'…' if len(snippet) > 300 else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mem-index",
        description="Project memory: .md -> chunk -> embed -> sqlite-vec -> search.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("warmup", help="One-time explicit model download + check.")

    pi = sub.add_parser("ingest", help="Reconcile .md -> index (hash-diff + prune).")
    pi.add_argument("--root", default=None, help="Project root (default: cwd).")

    ps = sub.add_parser("search", help="Semantic search over project memory.")
    ps.add_argument("query")
    ps.add_argument("--root", default=None, help="Project root (default: cwd).")
    ps.add_argument("--scope", choices=["project", "agent"])
    ps.add_argument("--agent", help="Filter to a single agent's memory.")
    ps.add_argument("-k", "--top-k", type=int, default=TOP_K)

    args = parser.parse_args(argv)
    if args.cmd == "warmup":
        return _cmd_warmup()
    if args.cmd == "ingest":
        return _cmd_ingest(args.root)
    return _cmd_search(args)


if __name__ == "__main__":
    raise SystemExit(main())
