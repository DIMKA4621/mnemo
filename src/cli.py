"""CLI entry points — the single engine behind hooks and (later) MCP.

  warmup            explicit one-time 2 GB model download + sanity check
  init [--root]     wire mnemo into a project (additive, idempotent)
  ingest [--root]   reconcile a project's .md -> its index (hook target)
  search [--root]   semantic search over a project's memory
  mcp               run the stdio MCP server (agent-callable tools)
  hook-postedit     PostToolUse target: reindex ONLY if a memory file changed
  hook-inject       UserPromptSubmit target: inject relevant memory
  embed-server      resident embedding helper (auto-started, not run by hand)

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
            "mnemo: changes detected but the model is not cached.\n"
            "Run `mnemo warmup` once, then this will work.",
            file=sys.stderr,
        )
        return 2
    reindex(root)
    return 0


def _cmd_hook_postedit() -> int:
    """PostToolUse target. Reads the hook JSON from stdin, and only runs a
    reconcile if the edited file is inside this project's memory tree.
    Anything else (code, README, ...) returns instantly — the engine is
    never spawned-into-work for unrelated edits. Always exit 0: a
    PostToolUse hook must never block the edit.
    """
    import json

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # no/invalid payload -> fail safe, do nothing

    ti = payload.get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("path") or ti.get("notebook_path")
    if not raw:
        return 0

    root = payload.get("cwd")  # project root at edit time
    paths = resolve(root)
    fp = Path(raw)
    if not fp.is_absolute():
        fp = Path(root or ".").joinpath(fp)
    fp = fp.resolve()

    mem = paths.project_memory.resolve()
    agt = paths.agent_memory.resolve()
    in_memory = fp.is_relative_to(mem) or fp.is_relative_to(agt)
    if not in_memory:
        return 0  # unrelated file -> instant no-op, DB never touched

    reindex(root)
    return 0


def _cmd_hook_inject() -> int:
    """UserPromptSubmit target. Reads hook JSON on stdin, embeds the prompt
    via the warm resident, searches THIS project's memory (gated), and
    prints relevant sections to stdout (Claude Code injects a
    UserPromptSubmit hook's stdout into the context). Best-effort: on any
    failure it skips with a one-line stderr note and never blocks.

    Wall-clock bounded by ``INJECT_BUDGET_S`` — exits gracefully under
    Claude Code's 30 s hook timeout instead of being SIGKILL-ed.
    """
    import json
    import time

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    prompt = (payload.get("prompt") or payload.get("user_prompt") or "").strip()
    if not prompt:
        return 0
    root = payload.get("cwd")  # project root at prompt time

    from .config import INJECT_BUDGET_S, INJECT_TOP_N
    from .embed_server import embed_query_via_server

    t0 = time.monotonic()

    def remaining() -> float:
        return INJECT_BUDGET_S - (time.monotonic() - t0)

    vec = embed_query_via_server(prompt, budget_s=max(0.5, remaining()))
    if vec is None:
        print(
            "mnemo: embedding helper unavailable — memory injection skipped",
            file=sys.stderr,
        )
        return 0  # never block the turn
    if remaining() <= 0:
        print("mnemo: inject budget exhausted after embed — skipped",
              file=sys.stderr)
        return 0

    hits = search(
        prompt, root=root, scope="project", qvec=vec, gate=True,
        top_k=INJECT_TOP_N,
    )
    if remaining() <= 0:
        print("mnemo: inject budget exhausted after search — skipped",
              file=sys.stderr)
        return 0
    if not hits:
        return 0  # nothing relevant -> inject nothing (no context noise)

    out = ['<project-memory source="mnemo">',
           "Curated project memory relevant to this prompt:"]
    for h in hits:
        snippet = " ".join(h.content.split())
        out.append(f"\n### {h.path} · {h.heading or '(no heading)'}\n{snippet}")
    out.append("</project-memory>")
    print("\n".join(out))
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
        prog="mnemo",
        description="Project memory: .md -> chunk -> embed -> sqlite-vec -> search.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("warmup", help="One-time explicit model download + check.")
    sub.add_parser("mcp", help="Run the stdio MCP server (agent tools).")
    sub.add_parser(
        "hook-postedit",
        help="PostToolUse target: reads hook JSON on stdin, reconciles "
        "only if the edited file is a memory file.",
    )
    sub.add_parser(
        "hook-inject",
        help="UserPromptSubmit target: reads hook JSON on stdin, injects "
        "relevant project memory via the warm helper.",
    )
    sub.add_parser(
        "embed-server",
        help="Resident embedding helper (loopback). Auto-started by "
        "hook-inject; not meant to be run by hand.",
    )

    pn = sub.add_parser(
        "init",
        help="Wire mnemo into a project: create the memory skeleton and "
        "additively merge .mcp.json / .claude/settings.json (idempotent, "
        "refuses on conflict, never touches CLAUDE.md).",
    )
    pn.add_argument("--root", default=None, help="Project root (default: cwd).")

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
    if args.cmd == "mcp":
        from .mcp_server import run
        run()
        return 0
    if args.cmd == "hook-postedit":
        return _cmd_hook_postedit()
    if args.cmd == "hook-inject":
        return _cmd_hook_inject()
    if args.cmd == "embed-server":
        from .embed_server import serve
        serve()
        return 0
    if args.cmd == "init":
        from .scaffold import init_project
        return init_project(args.root)
    if args.cmd == "ingest":
        return _cmd_ingest(args.root)
    return _cmd_search(args)


if __name__ == "__main__":
    raise SystemExit(main())
