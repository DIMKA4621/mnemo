"""The CLI — a thin client of the backend, plus the few genuinely local ops.

v3 splits the commands in two, and the split is the point (§11.1):

* **local**: `warmup`, `init`, `service *`, `autostart *`, `serve`,
  `embed-server`, `doctor`, `clean-orphans`. These either set the machine up,
  *are* the service, or operate on this machine's state directory — so they
  cannot go through it. `clean-orphans` in particular must work with the
  backend down, which is when someone goes looking at disk usage.
* **everything else** talks HTTP to the backend via `client.py`. There is one
  writer to an index and one search implementation, and this is a caller of
  both — not a second copy.

**Degradation is specified, not incidental.** Backend down → one line of
explanation and **exit code 3**, so a script can tell "the service is not
running" from "nothing was found" (exit 0) and from a real error (1). The
hooks never propagate anything: they exit 0 whatever happens, because a hook
that fails must not cost the user their turn.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import TOP_K

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_SERVICE_DOWN = 3


# --------------------------------------------------------------- helpers


def _client(timeout: float = 10.0):
    from .client import Client

    return Client(timeout=timeout)


def _run_api(fn) -> int:
    """Call the backend, turning its two failure modes into exit codes."""
    from .client import ApiFailure, ServiceDown

    try:
        fn(_client())
    except ServiceDown as exc:
        print(f"mnemo: {exc}\n"
              f"       start it with `mnemo service start`.", file=sys.stderr)
        return EXIT_SERVICE_DOWN
    except ApiFailure as exc:
        print(f"mnemo: {exc.code}: {exc.message}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def _bank_ref(explicit: str | None) -> str:
    """What to send as `bank`. Defaults to cwd — the backend resolves a path
    to the deepest bank containing it."""
    return explicit or str(Path.cwd())


# ------------------------------------------------------------ local commands


def _cmd_warmup() -> int:
    from .embedder import warmup

    print("Downloading / loading model (one-time, ~2.2 GB) ...")
    dim = warmup()
    print(f"READY — model cached, test embedding dim = {dim}")
    return EXIT_OK


def _cmd_doctor() -> int:
    """One place that answers "why is memory not working" (§11.1)."""
    from . import config, registry
    from .client import ServiceDown
    from .embed_server import server_is_up
    from .embedder import is_model_cached

    print(f"engine home      {config.USER_HOME}")
    print(f"state dir        {config.STATE_DIR}")
    print(f"python           {Path(sys.executable).as_posix()}")
    print(f"model cached     {is_model_cached()}")
    print(f"embed resident   {'up' if server_is_up() else 'down'} "
          f"({config.EMBED_HOST}:{config.EMBED_PORT})")

    client = _client(timeout=3.0)
    print(f"backend url      {client.base_url}")
    print(f"api token        {'present' if client.token else 'MISSING'}")
    try:
        health = client.health()
        # Two PIDs, and both are real. On Windows a venv's pythonw.exe is a
        # redirector that launches the interpreter as a child: service.pid
        # records what we spawned, service.json what actually serves the
        # socket. Printing one unlabelled number next to `service status`
        # printing the other is how a user ends up hunting the wrong process
        # in Task Manager.
        serving = health.get("pid")
        launcher = None
        try:
            from . import service_ctl

            identity = service_ctl.read_identity() or {}
            launcher = identity.get("pid")
        except Exception:  # noqa: BLE001 - diagnostics never fail
            pass
        pids = f"serving pid {serving}"
        if launcher and launcher != serving:
            pids += f", launcher pid {launcher}"
        print(f"backend          up ({pids}, {health.get('banks')} banks, "
              f"queue {health.get('queue_depth')})")
    except ServiceDown as exc:
        print(f"backend          DOWN — {exc}")
    try:
        banks = registry.load()
    except Exception as exc:  # noqa: BLE001
        print(f"registry         UNREADABLE — {exc}")
        return EXIT_ERROR
    print(f"banks            {len(banks)}")
    for bank in banks:
        flags = []
        if not bank.enabled:
            flags.append("disabled")
        if not bank.exists:
            flags.append("ROOT MISSING")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {bank.name:<20} {bank.root.as_posix()}{suffix}")

    # Reported, never cleaned here: `doctor` is a diagnosis. A diagnostic that
    # also deletes is one a user stops running when they only want to look.
    try:
        orphans = registry.orphan_indexes()
    except Exception as exc:  # noqa: BLE001 - diagnostics never fail
        print(f"orphan indexes   UNKNOWN — {exc}")
        return EXIT_OK
    if orphans:
        total = _human_bytes(sum(o.size for o in orphans))
        print(f"orphan indexes   {len(orphans)} ({total}) — "
              f"run `mnemo clean-orphans`")
    else:
        print("orphan indexes   none")
    return EXIT_OK


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{value:.0f} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _abbreviate(path: str) -> str:
    """Shorten a recorded root with ``~`` — these lines get long and the
    interesting part of a temp path is always its tail."""
    home = Path.home().as_posix()
    return f"~{path[len(home):]}" if path.startswith(home) else path


def _orphan_line(orphan) -> str:
    if orphan.error:
        where = f"(unreadable — {orphan.error})"
    elif orphan.root:
        where = _abbreviate(orphan.root)
        if orphan.root_exists:
            # Worth saying out loud: the folder is still there, so this may be
            # a bank someone meant to keep. Deleting only costs a reindex, but
            # the user should get to make that call knowingly.
            where += "   [root still on disk]"
    elif orphan.files is None:
        # No ``files`` table at all — not merely an old schema but a database
        # nothing ever finished writing. Worth distinguishing: it says the
        # index was abandoned mid-creation, not superseded.
        where = "(empty file — no index was ever written)"
    elif orphan.schema is None:
        where = "(pre-v3 index — no root recorded)"
    else:
        where = "(no root recorded)"
    files = "?" if orphan.files is None else str(orphan.files)
    unit = "file " if orphan.files == 1 else "files"
    return f"  {orphan.id}  {_human_bytes(orphan.size):>9}  {files:>3} {unit}  {where}"


def _cmd_clean_orphans(args: argparse.Namespace) -> int:
    """Delete index files that belong to no registered bank (§13, decision 25).

    Local, not an API call: the files live in this machine's state directory,
    nothing holds them open, and the command must work when the backend is
    down — which is exactly when someone goes looking at disk usage.
    """
    from . import registry

    try:
        orphans = registry.orphan_indexes()
    except Exception as exc:  # noqa: BLE001 - one refusal, one reason
        print(f"mnemo: cannot read the bank registry: {exc}\n"
              f"       refusing to guess which indexes are orphaned.",
              file=sys.stderr)
        return EXIT_ERROR

    if not orphans:
        print("no orphan indexes — every index file belongs to a registered bank")
        return EXIT_OK

    total = _human_bytes(sum(o.size for o in orphans))
    verb = "would remove" if args.dry_run else "will remove"
    print(f"{verb} {len(orphans)} orphan index"
          f"{'es' if len(orphans) != 1 else ''} ({total}):")
    for orphan in orphans:
        print(_orphan_line(orphan))

    if args.dry_run:
        return EXIT_OK

    if not args.yes:
        try:
            answer = input("proceed? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("nothing removed")
            return EXIT_OK

    removed, freed, failures = 0, 0, []
    for orphan in orphans:
        try:
            _, failed = registry.delete_index(orphan.id)
        except ValueError as exc:
            # Raised when the registry gained this bank between listing and
            # confirming. Skipped, and said out loud.
            print(f"  skipped {orphan.id}: {exc}")
            continue
        if failed:
            failures.extend(failed)
        else:
            removed += 1
            freed += orphan.size
    # Counted from what was actually deleted, not from what was listed: a
    # skipped or locked file must not be reported as space recovered.
    print(f"removed {removed} of {len(orphans)} ({_human_bytes(freed)} freed)")
    for path in failures:
        print(f"  locked: {path.as_posix()}", file=sys.stderr)
    return EXIT_ERROR if failures else EXIT_OK


# -------------------------------------------------------------- API commands


def _cmd_search(args: argparse.Namespace) -> int:
    from .client import ApiFailure, ServiceDown

    try:
        body = _client().search(
            _bank_ref(args.bank), args.query,
            top_k=args.top_k, path_prefix=args.path_prefix, face="cli",
        )
    except ServiceDown as exc:
        print(f"mnemo: {exc}\n       start it with `mnemo service start`.",
              file=sys.stderr)
        return EXIT_SERVICE_DOWN
    except ApiFailure as exc:
        print(f"mnemo: {exc.code}: {exc.message}", file=sys.stderr)
        return EXIT_ERROR

    # The status line first: "nothing indexed" and "no match" are different
    # answers and a human deserves to see which one they got.
    print(f"[{body['bank_name']} · {body['status']} · "
          f"queued={body['queued']} · chunks={body['chunk_count']}]")
    if body.get("degraded"):
        print(f"  (degraded: {body['degraded']})", file=sys.stderr)
    if not body["hits"]:
        if body["status"] == "indexing" and body["chunk_count"] == 0:
            print("Still building the first index — retry shortly.")
        elif body["status"] == "empty":
            print("Nothing indexed yet.")
        else:
            print("No relevant results.")
        return EXIT_OK
    for i, hit in enumerate(body["hits"], 1):
        print(f"\n[{i}] {hit['path']}  ·  {hit['heading'] or '(no heading)'}"
              f"  ·  score={hit['score']:.4f}")
        snippet = " ".join((hit.get("content") or "").split())
        print(f"    {snippet[:300]}{'…' if len(snippet) > 300 else ''}")
    return EXIT_OK


def _cmd_reindex(args: argparse.Namespace) -> int:
    def call(c):
        body = c.reindex(_bank_ref(args.bank), path=args.path, full=args.full)
        print(f"queued {len(body['task_ids'])} task(s); "
              f"{body['queued']} waiting.")

    return _run_api(call)


def _cmd_banks(args: argparse.Namespace) -> int:
    def call(c):
        if args.action == "list":
            banks = c.banks()
            if not banks:
                print("No banks registered.")
                return
            print(f"{'NAME':<20} {'STATUS':<9} {'FILES':>6} {'CHUNKS':>7}  ROOT")
            for b in banks:
                print(f"{b['name']:<20} {b['status']:<9} {b['files']:>6} "
                      f"{b['chunks']:>7}  {b['root']}")
        elif args.action == "add":
            info = c.add_bank(str(Path(args.path).expanduser().resolve()),
                              name=args.name, provider=args.provider)
            print(f"registered {info['name']}  ({info['id']})  {info['root']}")
        else:
            banks = {b["name"]: b for b in c.banks()}
            target = banks.get(args.path) or next(
                (b for b in banks.values() if b["id"] == args.path), None
            )
            if target is None:
                print(f"mnemo: no bank named {args.path!r}", file=sys.stderr)
                raise SystemExit(EXIT_ERROR)
            c.remove_bank(target["id"], drop_index=not args.keep_index)
            if args.keep_index:
                print(f"removed {target['name']} from the registry; its index "
                      f"file was kept on disk (--keep-index)")
            else:
                print(f"removed {target['name']} and deleted its index")

    return _run_api(call)


def _cmd_tree(args: argparse.Namespace) -> int:
    def call(c):
        body = c.tree(_bank_ref(args.bank), depth=args.depth)
        print(f"{body['root']}  ({body['files']} files, {body['dirs']} dirs)")

        def walk(node, indent):
            for child in node.get("children", []):
                pad = "  " * indent
                if child["type"] == "dir":
                    print(f"{pad}{child['name']}/")
                    walk(child, indent + 1)
                else:
                    heads = ", ".join(child.get("headings") or [])
                    print(f"{pad}{child['name']}" + (f"  — {heads}" if heads else ""))

        walk(body["tree"], 0)

    return _run_api(call)


def _short_key(key: str | None) -> str:
    """`local:intfloat/multilingual-e5-large:1024` -> `local:e5-large:1024`.

    Enough to see at a glance that two keys differ, short enough to sit in a
    table. Never a secret: the key is name:model:dim by construction.
    """
    if not key:
        return "—"
    parts = key.split(":")
    if len(parts) < 3:
        return key
    model = parts[1].rsplit("/", 1)[-1]
    return f"{parts[0]}:{model}:{parts[-1]}"


def _cmd_status() -> int:
    def call(c):
        body = c.status()
        svc, queue = body["service"], body["queue"]
        print(f"mnemo {svc['version']}  pid={svc['pid']}  port={svc['port']}  "
              f"up {svc['uptime_s']:.0f}s")
        provider = svc.get("provider") or "—"
        model = svc.get("provider_model")
        print(f"provider {provider}"
              + (f" ({model}, dim {svc.get('provider_dim')})" if model else "")
              + f"  embed "
              + ("reachable" if svc["embed"].get("reachable") else "DOWN"))
        if svc.get("provider_error"):
            print(f"  provider NOT CONFIGURED: {svc['provider_error']}")
        print(f"queue depth={queue['depth']} high={queue['high']} "
              f"normal={queue['normal']} low={queue['low']}")
        if queue.get("current"):
            cur = queue["current"]
            print(f"  current: {cur['kind']} {cur['path'] or ''} "
                  f"batch {cur['batch']}/{cur['batches']}")
        print()
        print(f"  {'BANK':<20} {'PROVIDER':<9} {'INDEX BUILT BY':<32} "
              f"{'STATUS':<9} {'CHUNKS':>7}")
        for b in body["banks"]:
            # Both the provider that WOULD index this bank and the one that
            # actually built the vectors in it. When they differ the next
            # reconcile re-embeds everything, and that is the moment a user
            # needs to be told why — not left watching 300 files rebuild.
            flag = "  <- REBUILD PENDING" if b.get("rebuild_pending") else ""
            if b.get("provider_error"):
                flag = f"  <- {b['provider_error']}"
            print(f"  {b['name']:<20} "
                  f"{(b.get('provider_active') or '—'):<9} "
                  f"{_short_key(b.get('index_provider_key')):<32} "
                  f"{b['status']:<9} {b['chunks']:>7}{flag}")

    return _run_api(call)


def _cmd_logs(args: argparse.Namespace) -> int:
    def call(c):
        body = c.logs(args.kind, bank=args.bank, since=args.since,
                      limit=args.n)
        print(f"{body['total']} event(s); showing {len(body['events'])}")
        for ev in body["events"]:
            if args.kind == "query":
                print(f"{ev['ts']}  {ev['face']:<5} {ev['status']:<9} "
                      f"n={ev['n_hits']:<3} {ev['took_ms']:.0f}ms  "
                      f"{ev['query'][:60]}")
            else:
                print(f"{ev['ts']}  {ev['kind']:<7} {ev['trigger']:<8} "
                      f"{ev['result']:<8} {ev['path'] or ''}"
                      f"{'  ' + ev['error'] if ev.get('error') else ''}")

    return _run_api(call)


def _cmd_ui() -> int:
    """Open the cabinet, handing the token over in the URL once (§9.1)."""
    import webbrowser

    client = _client()
    url = f"{client.base_url}/ui/?token={client.token}"
    print(url)
    webbrowser.open(url)
    return EXIT_OK


# ------------------------------------------------------------------- hooks


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Deprecated alias for `reindex` — and **never** a non-zero exit.

    A project adopted under v2 has `mnemo ingest` in its git-tracked
    `SessionStart` hook. Anything wired into a hook must be incapable of
    failing a session: exiting 3 because the backend happens to be down
    would show the user an error at every single session start, which is how
    a tool gets ripped out. So this reports and returns 0, exactly like
    `hook-inject` and `hook-postedit`.

    `mnemo init --migrate` removes the hook; until a project runs it, this
    path stays and stays harmless.
    """
    from .client import ApiFailure, ServiceDown

    print("mnemo: `ingest` is deprecated — use `mnemo reindex`.",
          file=sys.stderr)
    try:
        body = _client().reindex(_bank_ref(args.root), path=None, full=False)
    except ServiceDown as exc:
        print(f"mnemo: backend unavailable, nothing queued ({exc}).",
              file=sys.stderr)
        return EXIT_OK
    except ApiFailure as exc:
        print(f"mnemo: {exc.code}: {exc.message}", file=sys.stderr)
        return EXIT_OK
    print(f"queued {len(body['task_ids'])} task(s); {body['queued']} waiting.")
    return EXIT_OK


def _cmd_hook_postedit() -> int:
    """v2 shim. Always exit 0, immediately, doing nothing.

    Reindexing on edit is the watcher's job now. This survives only because
    already-adopted projects have `mnemo hook-postedit` in git-tracked
    settings; removing the subcommand would break them at the next edit.
    `mnemo init --migrate` drops the hook, and then this is unreachable.
    """
    return EXIT_OK


def _cmd_hook_inject() -> int:
    """UserPromptSubmit target: surface relevant memory, or get out of the way.

    A thin HTTP call now — no model, no index, no embedding in this process.
    Every failure path exits 0 in silence: a hook that blocks or errors costs
    the user their turn, and no memory is strictly better than that.

    **Sends `CLAUDE_PROJECT_DIR`, never `cwd`.** `registry.resolve()` maps a
    path to the *deepest* bank containing it, so a session whose cwd is a
    subdirectory would resolve to a plausible but wrong bank, silently — the
    worst failure available here, because the answers look real.
    """
    from .config import INJECT_TOP_N

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return EXIT_OK

    prompt = (payload.get("prompt") or payload.get("user_prompt") or "").strip()
    if not prompt:
        return EXIT_OK

    root = (
        os.environ.get("CLAUDE_PROJECT_DIR")
        or payload.get("project_dir")
        or payload.get("cwd")
    )
    if not root:
        return EXIT_OK

    from .client import Client

    try:
        # Short timeout on purpose: this sits in front of a human waiting for
        # a reply. Late memory is worse than none.
        #
        # autostart=False here, unlike every other face: bringing a cold
        # backend up takes seconds, and this prompt would spend all of them
        # waiting. Instead kick a start off in the background and return
        # empty — this turn gets no memory, the next one does.
        body = Client(timeout=5.0, autostart=False).search(
            str(root), prompt, top_k=INJECT_TOP_N, face="hook",
        )
    except Exception:  # noqa: BLE001 - never let a hook surface anything
        from .client import ensure_backend

        try:
            ensure_backend(wait=False)
        except Exception:  # noqa: BLE001
            pass
        return EXIT_OK

    hits = body.get("hits") or []
    if not hits:
        return EXIT_OK
    out = ['<project-memory source="mnemo">',
           "Curated project memory relevant to this prompt:"]
    for hit in hits:
        snippet = " ".join((hit.get("content") or "").split())
        out.append(f"\n### {hit['path']} · {hit['heading'] or '(no heading)'}\n"
                   f"{snippet}")
    out.append("</project-memory>")
    print("\n".join(out))
    return EXIT_OK


# Names the memory index may carry, in the order they are tried.
_INDEX_NAMES = ("MEMORY.md", "memory.md")
# What the hook is willing to paste into a session. `MEMORY.md` is meant to be
# an index under ~200 lines; if somebody let it grow into a store, the hook
# refuses to dump the whole thing and says where to look instead. A truncated
# index would be worse than none: it reads complete while missing entries.
_INDEX_MAX_BYTES = 32_768


def _cmd_memory_hook() -> int:
    """SessionStart target: hand over the memory index and the layout.

    This replaces the native `MEMORY.md` auto-load that a bank inside the
    repository does not get. Deliberately **not** a search: it injects a *map*,
    which tells the agent to go look, where injected search results read as
    memory already gathered.

    Reads the file **straight off the disk** — no HTTP, no backend, no model.
    So it cannot hang the start of a session and cannot fail because the
    service happens to be down. Every failure path exits 0 in silence, for the
    same reason `hook-inject` does: a hook that errors costs the user a turn.
    """
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    root = (
        os.environ.get("CLAUDE_PROJECT_DIR")
        or payload.get("project_dir")
        or payload.get("cwd")
        or os.getcwd()
    )
    mem = Path(root) / ".claude" / "memory"
    if not mem.is_dir():
        return EXIT_OK

    index: Path | None = None
    for name in _INDEX_NAMES:
        candidate = mem / name
        if candidate.is_file():
            index = candidate
            break

    out = ['<project-memory source="mnemo">']
    if index is None:
        out.append(f"Memory bank: {mem.as_posix()} — no index file yet. "
                   f"Create `MEMORY.md` there as a thin index (links + quick "
                   f"facts) when you first record something.")
    else:
        try:
            size = index.stat().st_size
            text = index.read_text(encoding="utf-8") if size <= _INDEX_MAX_BYTES else None
        except OSError:
            return EXIT_OK
        if text is None:
            out.append(f"Memory index {index.as_posix()} is {size // 1024} KB — "
                       f"too large to inject, and too large to be an index. "
                       f"Read it directly, and move its detail into "
                       f"`topics/`.")
        else:
            out.append(f"Index of this project's curated memory "
                       f"({index.as_posix()}):")
            out.append(text.rstrip())

    # The map, always — even when the index is missing or oversized, because
    # knowing the layout is what lets an agent search usefully.
    out.append(
        "\nLayout: `logs/YYYY-MM-DD.md` (day notes), `topics/<name>.md` (one "
        "concept per file), `agents/<role>/` (per-role memory). This index is "
        "a map, NOT the memory: search the bank with the `search` MCP "
        "tool before planning, changing architecture, debugging, or answering "
        "\"why is this like this\". Record what you learn back into these "
        "files."
    )
    out.append("</project-memory>")
    print("\n".join(out))
    return EXIT_OK


# -------------------------------------------------------------------- main


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mnemo",
        description="Project memory: .md -> chunk -> embed -> sqlite-vec -> search.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --- local ----------------------------------------------------------
    sub.add_parser("warmup", help="One-time explicit model download + check.")
    sub.add_parser("doctor", help="Diagnose venv, model, token, ports, banks.")

    co = sub.add_parser(
        "clean-orphans",
        help="Delete index files in state/ that belong to no registered bank.",
    )
    co.add_argument(
        "--dry-run", action="store_true",
        help="List what would be deleted and stop. Never asks, never deletes.",
    )
    co.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt (for scripts).",
    )
    sub.add_parser("embed-server", help="Resident embedding helper (internal).")
    sub.add_parser("hook-postedit", help="v2 shim: exits 0 (watcher reindexes).")
    sub.add_parser("hook-inject", help="UserPromptSubmit seed: inject top-N.")
    sub.add_parser("memory-hook",
                   help="SessionStart seed: inject MEMORY.md + the layout.")

    pn = sub.add_parser("init", help="Wire mnemo into a project (additive).")
    pn.add_argument("--root", default=None, help="Project root (default: cwd).")
    pn.add_argument(
        "--migrate", action="store_true",
        help="Also rewrite mnemo's OWN legacy wiring to the current form, and "
             "unwire hooks it no longer writes. Never touches a key mnemo did "
             "not author.",
    )
    # No hook is wired without one of these. Off by default because the
    # primary access to memory is MCP and the discipline lives in the rule.
    pn.add_argument(
        "--with-memory-hook", action="store_true",
        help="Wire the SessionStart seed: injects MEMORY.md + the layout.",
    )
    pn.add_argument(
        "--with-inject-hook", action="store_true",
        help="Wire the UserPromptSubmit seed: injects top-N search hits. Note "
             "it delivers memory before the task is stated, which reads as "
             "memory already gathered.",
    )

    pv = sub.add_parser("serve", help="Run the backend in the foreground.")
    pv.add_argument("--host", default=None)
    pv.add_argument("--port", type=int, default=None)

    psv = sub.add_parser("service", help="start | stop | status | restart.")
    psv.add_argument("action", choices=("start", "stop", "status", "restart"))
    psv.add_argument("--foreground", action="store_true")

    pas = sub.add_parser("autostart", help="enable | disable | status.")
    pas.add_argument("action", choices=("enable", "disable", "status"))

    # --- API clients ----------------------------------------------------
    ps = sub.add_parser("search", help="Semantic search over a bank.")
    ps.add_argument("query")
    ps.add_argument("--bank", default=None, help="Bank id, name or path.")
    ps.add_argument("--path-prefix", default=None)
    ps.add_argument("-k", "--top-k", type=int, default=TOP_K)

    pr = sub.add_parser("reindex", help="Queue a reindex.")
    pr.add_argument("--bank", default=None)
    pr.add_argument("--path", default=None, help="One file, relative to root.")
    pr.add_argument("--full", action="store_true", help="Rebuild from scratch.")

    pb = sub.add_parser("banks", help="list | add | remove.")
    pb.add_argument("action", choices=("list", "add", "remove"))
    pb.add_argument("path", nargs="?", default=None,
                    help="add: the root; remove: a name or id.")
    pb.add_argument("--name", default=None)
    pb.add_argument("--provider", default=None)
    pb.add_argument("--keep-index", action="store_true")

    pt = sub.add_parser("tree", help="Show a bank's memory tree.")
    pt.add_argument("--bank", default=None)
    pt.add_argument("--depth", type=int, default=0)

    sub.add_parser("status", help="Service, queue and bank status.")
    sub.add_parser("ui", help="Open the local web cabinet.")

    pl = sub.add_parser("logs", help="Service journal.")
    pl.add_argument("--kind", choices=("query", "index"), default="query")
    pl.add_argument("--bank", default=None)
    pl.add_argument("--since", default=None, help="ISO-8601 or epoch seconds.")
    pl.add_argument("-n", type=int, default=50)

    # --- deprecated -----------------------------------------------------
    pi = sub.add_parser("ingest", help="Deprecated alias for `reindex`.")
    pi.add_argument("--root", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    # Quiet SIGPIPE: `mnemo logs | head` should not traceback.
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass  # Windows has no SIGPIPE; some embeds restrict signal calls.

    # Memory is Ukrainian as often as English, and results are printed with
    # '·' / '…'. A default Windows console is cp1252, so writing a hit would
    # die with UnicodeEncodeError. stdin matters just as much: the hooks are
    # fed a JSON payload whose prompt is routinely Cyrillic. All three
    # streams, one rule.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass  # redirected to something that cannot be reconfigured

    args = _build_parser().parse_args(argv)
    cmd = args.cmd

    if cmd == "warmup":
        return _cmd_warmup()
    if cmd == "doctor":
        return _cmd_doctor()
    if cmd == "clean-orphans":
        return _cmd_clean_orphans(args)
    if cmd == "embed-server":
        from .embed_server import serve
        serve()
        return EXIT_OK
    if cmd == "hook-postedit":
        return _cmd_hook_postedit()
    if cmd == "hook-inject":
        return _cmd_hook_inject()
    if cmd == "memory-hook":
        return _cmd_memory_hook()
    if cmd == "init":
        from .scaffold import init_project
        seeds = (["memory"] if args.with_memory_hook else []) + \
                (["inject"] if args.with_inject_hook else [])
        return init_project(args.root, migrate=args.migrate, hooks=seeds)
    if cmd == "serve":
        from .api import run
        run(host=args.host, port=args.port)
        return EXIT_OK
    if cmd == "service":
        from . import service_ctl
        if args.action == "start":
            return service_ctl.start(foreground=args.foreground)
        if args.action == "stop":
            return service_ctl.stop()
        if args.action == "restart":
            return service_ctl.restart()
        return service_ctl.status()
    if cmd == "autostart":
        from . import autostart
        if args.action == "enable":
            return autostart.enable()
        if args.action == "disable":
            return autostart.disable()
        return autostart.status()

    if cmd == "search":
        return _cmd_search(args)
    if cmd == "reindex":
        return _cmd_reindex(args)
    if cmd == "banks":
        return _cmd_banks(args)
    if cmd == "tree":
        return _cmd_tree(args)
    if cmd == "status":
        return _cmd_status()
    if cmd == "ui":
        return _cmd_ui()
    if cmd == "logs":
        return _cmd_logs(args)
    if cmd == "ingest":
        return _cmd_ingest(args)
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
