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
    to the bank containing it, or to the one bank it contains.

    A path-looking ref is made absolute **here**, because the backend's cwd is
    its own: `--bank .claude/memory` is meaningful only where the user typed
    it. A bare word is left alone — it is a bank name, and joining a mistyped
    name to the cwd would turn a typo into a confident answer about whichever
    bank happens to enclose that directory.
    """
    if explicit is None:
        return str(Path.cwd())
    looks_like_path = explicit in (".", "..") or any(s in explicit for s in "/\\")
    if looks_like_path and not Path(explicit).expanduser().is_absolute():
        return str(Path(explicit).expanduser().resolve())
    return explicit


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
    # "down" is the normal state of a machine that has not searched yet — the
    # resident is started on demand and holds ~1.5 GB, so it does not sit
    # there from boot. Saying only "down" right after an install reads as a
    # broken install, and that is the first thing a new user sees.
    resident = ("up" if server_is_up() else "down (starts on first search)")
    print(f"embed resident   {resident} "
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

    _report_project_wiring(banks)
    return EXIT_OK


def _report_project_wiring(banks) -> None:
    """Projects whose mnemo wiring no longer matches this machine.

    Two ways a project ends up here, and both are invisible from inside it:

      * it carries a shape from an older generation — a stdio entry, a hook
        the watcher replaced — which only `--migrate` rewrites;
      * its wiring is current, but no registered bank covers it. That is the
        state every project is in after a v2→v3 upgrade or a reinstall:
        `banks.json` is the one thing that does not rebuild from the `.md`,
        so the token in the project's config addresses a bank that is gone.

    Deliberately a report. The commands touch someone else's working tree,
    which may be dirty, mid-rebase, or simply not something they want edited
    today — printing them keeps that decision where it belongs.
    """
    try:
        from .scaffold import adopted_projects

        projects = adopted_projects()
    except Exception as exc:  # noqa: BLE001 - diagnostics never fail
        print(f"project wiring   UNKNOWN — {exc}")
        return

    def covering(root: Path):
        for bank in banks:
            try:
                if bank.root.is_relative_to(root):
                    return bank
            except (OSError, ValueError):
                continue
        return None

    def why(proj) -> str | None:
        """Why this project needs rewiring, or None if it is fine."""
        if proj.migrate:
            extra = (f" +{len(proj.findings) - 1} more"
                     if len(proj.findings) > 1 else "")
            return f"{proj.findings[0]}{extra}"
        bank = covering(proj.root)
        if bank is None:
            return "no registered bank covers it"
        # A registered bank is not enough. Tokens are minted, never derived,
        # so a reinstall gives the same bank a new secret while the project's
        # config keeps the old one — the wiring looks right, points at a live
        # bank, and is rejected at the door. Nothing inside the project can
        # tell; the session just finds no memory tools.
        if proj.token and proj.token != bank.token:
            return f"its token is not the one bank {bank.name!r} now carries"
        return None

    reasons = [(p, why(p)) for p in projects]
    stale = [(p, r) for p, r in reasons if r is not None]
    if not stale:
        print(f"project wiring   {len(projects)} project(s), all current")
        return

    print(f"project wiring   {len(stale)} of {len(projects)} project(s) "
          f"need rewiring")
    for proj, reason in stale:
        print(f"  {proj.command()}")
        print(f"      {reason}")


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
    """Print the cabinet's URL, token filled in (§9.1). Opens nothing.

    It used to call `webbrowser.open` as well. Which browser that reaches is
    not a decision this command gets to make: it is whatever the OS has
    registered, in whatever profile happens to be signed in, and the URL
    carries the service token — the widest credential on the machine. A
    printed line goes exactly where the user is already looking, and they
    click it, or paste it into the browser they meant.
    """
    client = _client()
    print(f"{client.base_url}/ui/?token={client.token}")
    return EXIT_OK


# ------------------------------------------------------------------- hooks


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Deprecated alias for `reindex` — and **never** a non-zero exit.

    A project adopted under v2 has `mnemo ingest` in its git-tracked
    `SessionStart` hook. Anything wired into a hook must be incapable of
    failing a session: exiting 3 because the backend happens to be down
    would show the user an error at every single session start, which is how
    a tool gets ripped out. So this reports and returns 0, exactly like
    `hook-postedit`.

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



# -------------------------------------------------------------------- main


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mnemo",
        description="Project memory: .md -> chunk -> embed -> sqlite-vec -> "
                    "search. Curated markdown is the source of truth; the "
                    "index is derived, disposable and rebuilt automatically.",
        epilog="A bank defaults to the current directory: mnemo finds the "
               "bank containing it, or the one bank it contains. Say --bank "
               "<name> when a folder holds several.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    # --- local ----------------------------------------------------------
    sub.add_parser(
        "warmup",
        help="Download the embedding model (~2.2 GB). The ONLY thing that "
             "does — no hook, service or search ever downloads it for you.",
    )
    sub.add_parser(
        "doctor",
        help="Answer 'why is memory not working': engine, venv, model cache, "
             "embedding resident, token, backend, banks, orphan indexes. "
             "Reads only — it never changes anything.",
    )

    co = sub.add_parser(
        "clean-orphans",
        help="Delete index files in state/ that belong to no registered bank "
             "— left behind by removed banks, edited roots and v2. Shows the "
             "list and asks first; nothing is ever deleted automatically.",
    )
    co.add_argument(
        "--dry-run", action="store_true",
        help="List what would be deleted and stop. Never asks, never deletes.",
    )
    co.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt (for scripts).",
    )
    # Spawn targets. Hidden from `--help`, not removed: nothing types them,
    # but something calls them. `serve` is what `service start` spawns;
    # `embed-server` is what the backend spawns. `hook-postedit` is a shim
    # whose entire job is to keep an already-wired hook from failing, so
    # deleting it would cause exactly what it exists to prevent — it is the
    # last hook entry point left, and it does nothing but exit 0.
    sub.add_parser("embed-server")
    sub.add_parser("hook-postedit")

    pn = sub.add_parser(
        "init",
        help="Wire mnemo into a project: seed .claude/memory + the memory "
             "rule, register the bank, mint its token and write the MCP "
             "entry. Additive and idempotent; refuses rather than write a "
             "token into a git-tracked file, and writes NOTHING when it does.",
    )
    pn.add_argument("--root", default=None, help="Project root (default: cwd).")
    pn.add_argument(
        "--yes", action="store_true",
        help="Answer yes to the one question init can ask: taking a "
             "git-tracked .mcp.json / .mcp.env out of the index so a bank "
             "token may be written into it. Without a terminal, init never "
             "assumes it.",
    )
    pn.add_argument(
        "--migrate", action="store_true",
        help="Also rewrite mnemo's OWN legacy wiring to the current form, and "
             "unwire every hook it used to write. Never touches a key mnemo "
             "did not author.",
    )

    pv = sub.add_parser("serve")
    pv.add_argument("--host", default=None, help="Bind address (default: loopback).")
    pv.add_argument("--port", type=int, default=None, help="Port (default: 8918).")

    psv = sub.add_parser(
        "service",
        help="Manage the backend process: start it detached and windowless, "
             "stop it, or report who is holding the port.",
    )
    psv.add_argument(
        "action", choices=("start", "stop", "status", "restart"),
        help="start: spawn it detached · stop: end it · status: who serves "
             "and since when · restart: stop then start.",
    )
    psv.add_argument(
        "--foreground", action="store_true",
        help="start only: run the backend in THIS terminal instead. Writes no "
             "state files and is invisible to stop/status on purpose — "
             "Ctrl-C is the control. For debugging.",
    )

    pas = sub.add_parser(
        "autostart",
        help="Bring the backend up at logon, silently (a hidden Task "
             "Scheduler task on Windows, systemd --user on Linux).",
    )
    pas.add_argument(
        "action", choices=("enable", "disable", "status"),
        help="enable: register it · disable: remove it · status: is it "
             "registered and what does it run.",
    )

    # --- API clients ----------------------------------------------------
    _BANK_HELP = ("Bank id, name, or an absolute path in or above one. "
                  "Default: the current directory.")

    ps = sub.add_parser(
        "search",
        help="Search a bank. Vectors find meaning, FTS5 finds words, and RRF "
             "blends both. Never blocks: prints the index status alongside "
             "the hits, so 'nothing found' and 'still building' stay "
             "distinguishable.",
    )
    ps.add_argument("query", help="What to look for. Any language.")
    ps.add_argument("--bank", default=None, help=_BANK_HELP)
    ps.add_argument(
        "--path-prefix", default=None,
        help="Narrow to a subfolder, e.g. logs or topics. Navigation, not an "
             "access boundary.",
    )
    ps.add_argument("-k", "--top-k", type=int, default=TOP_K,
                    help=f"How many sections to return (default: {TOP_K}).")

    pr = sub.add_parser(
        "reindex",
        help="Queue a reindex. Rarely needed by hand — the watcher picks up "
             "a saved file within seconds. This forces the issue.",
    )
    pr.add_argument("--bank", default=None, help=_BANK_HELP)
    pr.add_argument("--path", default=None,
                    help="One file, relative to the bank root. Default: the "
                         "whole bank.")
    pr.add_argument(
        "--full", action="store_true",
        help="Wipe the index and rebuild it — minutes, proportional to bank "
             "size. Without this only changed files are re-embedded.",
    )

    pb = sub.add_parser(
        "banks",
        help="The registry of memory roots this machine serves.",
    )
    pb.add_argument(
        "action", choices=("list", "add", "remove"),
        help="list: name, status, files, chunks, root · add: register a "
             "folder of .md · remove: unregister it (the .md are never "
             "touched).",
    )
    pb.add_argument("path", nargs="?", default=None,
                    help="add: the root folder; remove: a name or id.")
    pb.add_argument("--name", default=None,
                    help="add: what to call it. Default: derived from the "
                         "path; a clash gets a -2 suffix.")
    pb.add_argument("--provider", default=None,
                    help="add: embedding provider override (default: local).")
    pb.add_argument("--keep-index", action="store_true",
                    help="remove: unregister but leave the index file behind. "
                         "It becomes an orphan — see clean-orphans.")

    pt = sub.add_parser(
        "tree",
        help="Print a bank's layout with each file's headings — the map that "
             "tells you what is worth searching for.",
    )
    pt.add_argument("--bank", default=None, help=_BANK_HELP)
    pt.add_argument("--depth", type=int, default=0,
                    help="Levels to descend. 0 (default) means all.")

    sub.add_parser(
        "status",
        help="One screen: backend pid, port and uptime, provider, queue "
             "depth, and every bank's index state.",
    )
    sub.add_parser(
        "ui",
        help="Print the link to the local cabinet — banks, file tree, chunk "
             "boundaries, reindex buttons, journal. Token filled in; opens "
             "no browser.",
    )

    pl = sub.add_parser(
        "logs",
        help="The service journal: what was searched and what was indexed, "
             "newest first.",
    )
    pl.add_argument("--kind", choices=("query", "index"), default="query",
                    help="query: searches (default) · index: indexing runs.")
    pl.add_argument("--bank", default=None, help="Only this bank. " + _BANK_HELP)
    pl.add_argument("--since", default=None, help="ISO-8601 or epoch seconds.")
    pl.add_argument("-n", type=int, default=50,
                    help="How many events (default: 50).")

    # --- deprecated -----------------------------------------------------
    # Hidden rather than deleted: it still works, and still says it is
    # deprecated when used.
    pi = sub.add_parser("ingest")
    pi.add_argument("--root", default=None, help="Bank root. Default: cwd.")
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
    if cmd == "init":
        from .scaffold import init_project
        return init_project(args.root, migrate=args.migrate,
                            yes=args.yes)
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
