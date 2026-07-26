"""CLI entry points — the single engine behind hooks and (later) MCP.

  warmup            explicit one-time 2 GB model download + sanity check
  init [--root]     wire mnemo into a project (additive, idempotent)
  ingest [--root]   reconcile a bank's .md -> its index (hook target)
  search [--root]   semantic search over a bank
  mcp               run the stdio MCP server (agent-callable tools)
  hook-postedit     PostToolUse target: reindex ONLY if a bank .md changed
  hook-inject       UserPromptSubmit target: inject relevant memory
  embed-server      resident embedding helper (auto-started, not run by hand)
  projects          list known projects (hash → cwd → last inject → log size)
  serve             run the backend in the foreground (the service's target)
  service …         start / stop / status / restart the background service
  autostart …       enable / disable / status of per-OS start-at-login

`--root` is the **bank root** and defaults to the current directory: a bank
is one folder, flat, and every `.md` under it belongs to the same index.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import TOP_K, resolve
from .embedder import warmup
from .index import pending_embeddings, reindex
from .providers import get_provider
from .search import search


def _cmd_warmup() -> int:
    print(f"Downloading / loading model (one-time, ~2.2 GB) ...")
    dim = warmup()
    print(f"READY — model cached, test embedding dim = {dim}")
    return 0


def _cmd_ingest(root: Path) -> int:
    # Never silently pull 2 GB inside a hook: refuse if work needs the
    # model but the provider cannot embed (no cached model AND no reachable
    # resident). A live resident (local or a shared/remote one via
    # MNEMO_EMBED_HOST) means the model is never loaded here — proceed.
    if pending_embeddings(root) and not get_provider().health():
        print(
            "mnemo: changes detected, but the model is not cached and no "
            "embedding resident is reachable.\n"
            "Run `mnemo warmup` once, or start / point to a resident "
            "(MNEMO_EMBED_HOST).",
            file=sys.stderr,
        )
        return 2
    reindex(root)
    return 0


def _cmd_hook_postedit() -> int:
    """PostToolUse target. Reads the hook JSON from stdin, and only runs a
    reconcile if the edited file is a markdown file inside the bank.
    Anything else (code, images, ...) returns instantly — the engine is
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

    root = payload.get("cwd")  # bank root at edit time
    paths = resolve(root)
    fp = Path(raw)
    if not fp.is_absolute():
        fp = Path(root or ".").joinpath(fp)
    fp = fp.resolve()

    if fp.suffix.lower() != ".md" or not fp.is_relative_to(paths.root):
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

    Every exit writes one JSONL line via ``inject_log`` so MIN_SIM /
    INJECT_TOP_N / gate behaviour can be tuned from real data.
    """
    import json
    import time

    from .inject_log import log_inject

    t0 = time.monotonic()

    def elapsed_ms() -> float:
        return (time.monotonic() - t0) * 1000.0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        log_inject(
            status="skipped_bad_payload", cwd=None, prompt="",
            total_ms=elapsed_ms(), note="stdin was not valid JSON",
        )
        return 0

    prompt = (payload.get("prompt") or payload.get("user_prompt") or "").strip()
    root = payload.get("cwd")
    if not prompt:
        log_inject(
            status="skipped_empty_prompt", cwd=root, prompt="",
            total_ms=elapsed_ms(),
        )
        return 0

    from .config import INJECT_BUDGET_S, INJECT_TOP_N
    from .embed_server import embed_query_via_server

    def remaining() -> float:
        return INJECT_BUDGET_S - (time.monotonic() - t0)

    t_embed_start = time.monotonic()
    vec = embed_query_via_server(prompt, budget_s=max(0.5, remaining()))
    embed_ms = (time.monotonic() - t_embed_start) * 1000.0
    if vec is None:
        print(
            "mnemo: embedding helper unavailable — memory injection skipped",
            file=sys.stderr,
        )
        log_inject(
            status="skipped_embed_unavailable", cwd=root, prompt=prompt,
            total_ms=elapsed_ms(), embed_ms=embed_ms,
        )
        return 0
    if remaining() <= 0:
        print("mnemo: inject budget exhausted after embed — skipped",
              file=sys.stderr)
        log_inject(
            status="skipped_budget_after_embed", cwd=root, prompt=prompt,
            total_ms=elapsed_ms(), embed_ms=embed_ms,
        )
        return 0

    t_search_start = time.monotonic()
    hits = _search_bank(root, prompt, qvec=vec, gate=True, top_k=INJECT_TOP_N)
    search_ms = (time.monotonic() - t_search_start) * 1000.0
    hit_records = [
        {
            "path": h.path,
            "heading": h.heading or None,
            "score": round(h.score, 6),
            "sim": None if h.sim is None else round(h.sim, 4),
        }
        for h in hits
    ]

    if remaining() <= 0:
        print("mnemo: inject budget exhausted after search — skipped",
              file=sys.stderr)
        log_inject(
            status="skipped_budget_after_search", cwd=root, prompt=prompt,
            total_ms=elapsed_ms(), embed_ms=embed_ms, search_ms=search_ms,
            hits=hit_records,
        )
        return 0
    if not hits:
        log_inject(
            status="ok_no_hits", cwd=root, prompt=prompt,
            total_ms=elapsed_ms(), embed_ms=embed_ms, search_ms=search_ms,
            hits=[],
        )
        return 0  # nothing relevant -> inject nothing (no context noise)

    out = ['<project-memory source="mnemo">',
           "Curated project memory relevant to this prompt:"]
    for h in hits:
        snippet = " ".join(h.content.split())
        out.append(f"\n### {h.path} · {h.heading or '(no heading)'}\n{snippet}")
    out.append("</project-memory>")
    print("\n".join(out))
    log_inject(
        status="ok", cwd=root, prompt=prompt,
        total_ms=elapsed_ms(), embed_ms=embed_ms, search_ms=search_ms,
        hits=hit_records,
    )
    return 0


def _cmd_projects() -> int:
    """List known projects: hash → path → last activity → log size.

    Derived from ``state/logs/*.log`` (every JSONL line carries cwd), so
    no extra manifest is maintained. A project with an index DB but zero
    inject history is listed as (no log entries yet).
    """
    import json
    from .config import INJECT_LOG_DIR, STATE_DIR

    rows: list[tuple[str, str, str, int]] = []
    seen_hashes: set[str] = set()

    if INJECT_LOG_DIR.is_dir():
        for log in sorted(INJECT_LOG_DIR.glob("*.log")):
            phash = log.stem
            size = log.stat().st_size
            cwd, ts = "(unknown)", "—"
            try:
                with log.open("rb") as fh:
                    # Read last line cheaply (small files; logs are KB-sized).
                    last = fh.read().splitlines()[-1] if size else b""
                if last:
                    rec = json.loads(last)
                    cwd = rec.get("cwd") or "(unknown)"
                    ts = rec.get("ts") or "—"
            except (OSError, ValueError, IndexError):
                pass
            rows.append((phash, cwd, ts, size))
            seen_hashes.add(phash)

    if STATE_DIR.is_dir():
        for db in sorted(STATE_DIR.glob("*.db")):
            if db.stem not in seen_hashes:
                rows.append((db.stem, "(no log entries yet)", "—", 0))

    if not rows:
        print("No mnemo projects found.")
        return 0

    print(f"{'HASH':<18} {'LAST':<26} {'LOG':>9}  CWD")
    for phash, cwd, ts, size in rows:
        size_h = f"{size/1024:.1f}K" if size else "—"
        print(f"{phash:<18} {ts:<26} {size_h:>9}  {cwd}")
    return 0


def _search_bank(root, query: str, **kwargs):
    """Open a bank read-only and search it.

    `search()` is pure and takes an open connection, so the caller owns the
    lifecycle. Read-only on purpose: a search must never create or migrate an
    index — an absent one simply has nothing to say. Phase 4 replaces this
    with an HTTP call to the backend.
    """
    from .store import connect

    paths = resolve(root)
    if not paths.db.exists():
        return []
    conn = connect(paths.db, ensure=False)
    try:
        return search(conn, query, **kwargs)
    finally:
        conn.close()


def _cmd_search(args: argparse.Namespace) -> int:
    hits = _search_bank(
        args.root,
        args.query,
        path_prefix=args.path_prefix,
        top_k=args.top_k,
    )
    if not hits:
        print("No relevant results.")
        return 0
    for i, h in enumerate(hits, 1):
        print(
            f"\n[{i}] {h.path}  ·  {h.heading or '(no heading)'}  ·  "
            f"score={h.score:.4f}"
        )
        snippet = " ".join(h.content.split())
        print(f"    {snippet[:300]}{'…' if len(snippet) > 300 else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Quiet SIGPIPE: `mnemo projects | head` should not traceback.
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass  # Windows has no SIGPIPE; some embeds restrict signal calls.

    # Memory is Ukrainian as often as English, and results are printed with
    # '·' / '…'. A default Windows console is cp1252, so writing a hit would
    # die with UnicodeEncodeError. Force UTF-8 and replace anything the
    # terminal genuinely cannot render — a mangled glyph beats a traceback.
    #
    # stdin matters just as much: the hooks are fed a JSON payload whose
    # prompt is routinely Cyrillic. Decoded as cp1252 it arrives as mojibake
    # with lone surrogates, which then poisons the search query and blows up
    # the log writer downstream. All three streams, one rule.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass  # redirected to something that cannot be reconfigured

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
    sub.add_parser(
        "projects",
        help="List known projects (hash, path, last activity, log size).",
    )

    pn = sub.add_parser(
        "init",
        help="Wire mnemo into a project: create the memory skeleton and "
        "additively merge .mcp.json / .claude/settings.json (idempotent, "
        "refuses on conflict, never touches CLAUDE.md).",
    )
    pn.add_argument("--root", default=None, help="Project root (default: cwd).")

    # --- process lifecycle (block L, platform-dev) ---------------------
    pv = sub.add_parser(
        "serve",
        help="Run the backend in the foreground (what `service start` "
        "launches in a windowless child; not usually run by hand).",
    )
    pv.add_argument("--host", default=None, help="Bind address (default: loopback).")
    pv.add_argument("--port", type=int, default=None, help="Port (default: 8918).")

    psv = sub.add_parser(
        "service",
        help="Control the background service: start | stop | status | restart.",
    )
    psv.add_argument(
        "action",
        choices=("start", "stop", "status", "restart"),
    )
    psv.add_argument(
        "--foreground",
        action="store_true",
        help="start only: run in this terminal instead of detaching.",
    )

    pas = sub.add_parser(
        "autostart",
        help="Start the service at login: enable | disable | status.",
    )
    pas.add_argument("action", choices=("enable", "disable", "status"))

    pi = sub.add_parser("ingest", help="Reconcile .md -> index (hash-diff + prune).")
    pi.add_argument("--root", default=None, help="Project root (default: cwd).")

    ps = sub.add_parser("search", help="Semantic search over a bank.")
    ps.add_argument("query")
    ps.add_argument("--root", default=None, help="Bank root (default: cwd).")
    ps.add_argument(
        "--path-prefix",
        default=None,
        help="Narrow to a folder (or file) inside the bank, e.g. 'logs'.",
    )
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
    if args.cmd == "projects":
        return _cmd_projects()
    if args.cmd == "serve":
        from .api import run
        run(host=args.host, port=args.port)
        return 0
    if args.cmd == "service":
        from . import service_ctl
        if args.action == "start":
            return service_ctl.start(foreground=args.foreground)
        if args.action == "stop":
            return service_ctl.stop()
        if args.action == "restart":
            return service_ctl.restart()
        return service_ctl.status()
    if args.cmd == "autostart":
        from . import autostart
        if args.action == "enable":
            return autostart.enable()
        if args.action == "disable":
            return autostart.disable()
        return autostart.status()
    if args.cmd == "init":
        from .scaffold import init_project
        return init_project(args.root)
    if args.cmd == "ingest":
        return _cmd_ingest(args.root)
    return _cmd_search(args)


if __name__ == "__main__":
    raise SystemExit(main())
