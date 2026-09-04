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


def _client(timeout: float = 10.0, *, autostart: bool = True):
    from .client import Client

    return Client(timeout=timeout, autostart=autostart)


def _run_api(fn, *, autostart: bool = True) -> int:
    """Call the backend, turning its two failure modes into exit codes."""
    from .client import ApiFailure, ServiceDown

    try:
        fn(_client(autostart=autostart))
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


def _providers_in_use(banks) -> tuple[str, list[str]]:
    """``(machine default, providers a bank names for itself)``.

    Both halves matter. The machine setting is not the whole answer — a bank
    carries an optional ``provider`` field that overrides it — so "is the local
    model needed on this machine" is a question about the union, not about
    ``settings.provider()`` alone.
    """
    from . import settings

    machine = settings.provider()
    overrides = sorted({b.provider for b in banks if b.provider and b.provider != machine})
    return machine, overrides


def _banks_quietly() -> list:
    """The registry, or an empty list. Callers here only want to know which
    providers are in play; an unreadable registry is `doctor`'s story to tell."""
    try:
        from . import registry

        return registry.load()
    except Exception:  # noqa: BLE001 - never the reason a command fails
        return []


def _cmd_warmup(args: argparse.Namespace) -> int:
    machine, overrides = _providers_in_use(_banks_quietly())
    in_use = {machine, *overrides}
    if "local" not in in_use and not getattr(args, "force", False):
        # 2.2 GB for a model nothing would load. The download is the one thing
        # this command does, so doing it anyway "just in case" is the whole
        # cost of the mistake — and the explicit-warmup invariant cuts both
        # ways: never implicitly, and not for a provider that does not use it.
        print(f"Nothing to download — this machine embeds through `{machine}`, "
              f"which calls an endpoint instead of loading a local model.")
        print("Run `mnemo warmup --force` to cache it anyway (e.g. before "
              "switching back to `local`).")
        return EXIT_OK

    from . import config, engine_update

    space = engine_update.check_disk_space(
        model_cached=False, include_version_size=False, target=config.MODEL_CACHE
    )
    if not space.ok:
        print(f"mnemo: {engine_update.InsufficientDiskSpace(space)}", file=sys.stderr)
        return EXIT_ERROR

    from .embedder import warmup

    print("Downloading / loading model (one-time, ~2.2 GB) ...")
    dim = warmup()
    print(f"READY — model cached, test embedding dim = {dim}")
    if "local" not in in_use:
        print(f"NOTE — the active provider is `{machine}`; this model is cached "
              f"but not in use.")
    return EXIT_OK


def _cmd_doctor() -> int:
    """One report, rendered for the terminal from the console's same data."""
    from . import diagnostics

    report = diagnostics.collect()
    print(diagnostics.render_text(report))
    return EXIT_OK if report["registry"].get("ok") else EXIT_ERROR


def _cmd_clean_orphans(args: argparse.Namespace) -> int:
    """Delete index files that belong to no registered bank (§13, decision 25).

    Local, not an API call: the files live in this machine's state directory,
    nothing holds them open, and the command must work when the backend is
    down — which is exactly when someone goes looking at disk usage.
    """
    from . import diagnostics, registry

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

    total = diagnostics.human_bytes(sum(item.size for item in orphans))
    verb = "would remove" if args.dry_run else "will remove"
    print(f"{verb} {len(orphans)} orphan index"
          f"{'es' if len(orphans) != 1 else ''} ({total}):")
    for orphan in orphans:
        print(diagnostics.orphan_line(diagnostics.orphan_json(orphan)))

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

    try:
        result = diagnostics.delete_orphans(item.id for item in orphans)
    except diagnostics.OrphanCleanupRefused as exc:
        print(f"mnemo: {exc}\n       nothing was removed.", file=sys.stderr)
        return EXIT_ERROR

    for item in result["skipped"]:
        print(f"  skipped {item['id']}: {item['reason']}")
    for item in result["locked"]:
        for path in item["paths"]:
            print(f"  locked: {path}", file=sys.stderr)
    print(
        f"removed {len(result['removed'])} of {len(orphans)} "
        f"({diagnostics.human_bytes(result['freed_bytes'])} freed)"
    )
    return EXIT_ERROR if result["locked"] else EXIT_OK


def _cmd_update_apply() -> int:
    """`update-apply` — self-update step 8: stop -> switch -> start -> health
    -> rollback. Hidden (see the bare ``add_parser`` above); spawned
    detached by the future ``/api/update/apply`` handler (step 9), runnable
    by hand for diagnostics.

    **Never trusts this process's own interpreter OR working directory to
    find what should serve after a switch.** This command's own process is
    dispatched under whichever venv `current` pointed at BEFORE the switch,
    and both its interpreter identity (``sys.executable``) and its ``cwd``
    are fixed for the process's whole lifetime — so every
    ``service_ctl.start()`` call below passes an EXPLICIT
    ``service_ctl.target_for_version()`` (argv AND cwd), computed from the
    tag just switched to/from, never the default target. Confirmed by two
    real runs, not by reasoning: (1) starting with the default target after
    a switch kept the OLD venv serving even though `current` already
    pointed at the new one; (2) passing only a new *interpreter* still
    silently ran the OLD *code* — ``-m src.cli`` resolves ``src`` against
    the child's ``cwd``, which beat an explicit ``PYTHONPATH`` unconditionally
    in a real experiment, so the interpreter fix alone was incomplete until
    ``target_for_version()`` also returned ``cwd`` and ``start()`` grew a
    ``cwd`` parameter to carry it. See ``service_ctl.windowless_python()``'s
    and ``service_ctl.target_for_version()``'s docstrings, and the design
    topic, for the full story.

    Tag selection: reads ``engine_version.json``'s ``last_check`` — a tag is
    "ready to apply" only when ``update_available`` is true (``latest_tag``
    differs from what is currently installed) AND a ``VERSION`` marker
    matching that tag exists under ``versions/<tag>/`` (proof
    ``stage_release()`` actually finished building it, not just that GitHub
    has a newer release). Neither engine_update.py nor the design topic
    names an explicit "staged and ready" field beyond this, so this is an
    inference from the two facts that ARE recorded — flagged as such in the
    step-8 report rather than decided silently.

    Exit codes (this command's own, not service_ctl's or cli.py's): 0 =
    applied, new tag healthy · 1 = apply failed, rollback succeeded (old tag
    healthy again) · 2 = nothing staged/ready to apply · 3 = apply AND
    rollback both failed health — the service is down, and `mnemo service
    status` already makes that visible, so nothing extra is hidden here.
    """
    from . import engine_update, service_ctl

    state = engine_update.read_state()
    last_check = state.get("last_check") or {}
    tag = last_check.get("latest_tag")
    if not tag or not last_check.get("update_available"):
        print("mnemo update-apply: no update available to apply "
              "(nothing in engine_version.json's last_check says one is ready)")
        return 2

    # Recorded by the API process (engine_update.set_pending_trigger) right
    # before it spawned this process -- "auto" only if THIS tag's pending
    # trigger says so, "manual" otherwise (unknown/stale origin is never
    # silently treated as auto for blacklist bookkeeping). Read once, up
    # front: every finish_apply(...) call site below reports its outcome
    # through the same trigger.
    trigger = engine_update.read_pending_trigger(tag)

    version_dir = service_ctl.versions_dir() / tag
    marker = version_dir / "VERSION"
    try:
        # utf-8-sig: transparently strips a BOM if one is present (some
        # writers add it, stage_release()'s plain write_text() does not) and
        # is otherwise identical to plain utf-8. A marker mismatch should
        # mean "wrong tag", never "right tag, wrong byte order mark".
        marker_tag = marker.read_text(encoding="utf-8-sig").strip()
    except OSError:
        print(f"mnemo update-apply: {tag} is not staged — no VERSION marker "
              f"at {marker} (run stage_release first)")
        return 2
    if marker_tag != tag:
        # Sanity, not security: the archive's integrity rests on TLS, not
        # this check. This only catches a mismatched/corrupted staging dir.
        print(f"mnemo update-apply: VERSION marker at {marker} says "
              f"{marker_tag!r}, expected {tag!r} — refusing to apply")
        return 2

    prev_tag = service_ctl.current_tag()

    engine_update.start_apply(tag, trigger=trigger)
    print(f"mnemo update-apply: applying {tag} (currently {prev_tag or '(none)'})")

    service_ctl.stop()

    with service_ctl.update_lock():
        service_ctl.switch_current(tag)
    print(f"mnemo update-apply: current -> {tag}")

    spawn = service_ctl.target_for_version(version_dir)
    rc = service_ctl.start(target=spawn.argv, cwd=spawn.cwd, wait_ready=True)
    if rc == service_ctl.EXIT_OK:
        # Republish bin\ from THIS version, not only on the first install:
        # the launcher exe's shebang is baked to a specific venv at build
        # time, and that venv is exactly what retention eventually deletes
        # -- see publish_launchers()'s docstring (self-update step 12, bug
        # A). Never on rollback (below): the version a rollback returns to
        # is what bin\ already names, nothing there is stale. A failure
        # here does not undo an otherwise-healthy switch -- the backend is
        # fine either way, only the human-facing `mnemo` command would be
        # stale -- so it is reported, not fatal.
        try:
            skipped = service_ctl.publish_launchers(version_dir)
            if skipped:
                # Most commonly: whichever exe dispatched THIS update-apply
                # process is its own running image, and Windows refuses to
                # overwrite an executable while it is mapped as one. Not
                # fatal — the other exe still got refreshed, and this one
                # catches up next time.
                print(f"mnemo update-apply: bin\\ partially republished from "
                      f"{tag} (still stale: {', '.join(skipped)} — likely "
                      f"in use by this very process; catches up on the "
                      f"next successful apply)")
            else:
                print(f"mnemo update-apply: bin\\ republished from {tag}")
        except OSError as exc:
            print(f"mnemo update-apply: WARNING - could not republish bin\\ "
                  f"from {tag}: {exc}\n"
                  f"       the service is healthy, but the `mnemo` command "
                  f"may be stale; run install.ps1 or fix bin\\ by hand")

        engine_update.record_installed(
            tag=tag, commit=None, status=engine_update.STATUS_ACTIVE
        )
        removed = service_ctl.prune_versions()
        engine_update.finish_apply(tag=tag, result="applied", trigger=trigger)
        if trigger == "auto":
            engine_update.record_auto_outcome(tag=tag, result="applied")
        pruned_note = f"; pruned {', '.join(removed)}" if removed else ""
        print(f"mnemo update-apply: {tag} is active and healthy{pruned_note}")
        return EXIT_OK

    print(f"mnemo update-apply: {tag} did not become healthy (rc={rc}); "
          f"rolling back")

    if prev_tag is None:
        print("mnemo update-apply: no previous version recorded — cannot "
              "roll back")
        no_rollback_error = "health check failed and there is no rollback target"
        engine_update.finish_apply(
            tag=tag, result="failed", error=no_rollback_error, trigger=trigger
        )
        if trigger == "auto":
            engine_update.record_auto_outcome(
                tag=tag, result="failed", error=no_rollback_error
            )
        return service_ctl.EXIT_DOWN

    with service_ctl.update_lock():
        service_ctl.switch_current(prev_tag)
    print(f"mnemo update-apply: current -> {prev_tag} (rollback)")

    rollback_spawn = service_ctl.target_for_version(service_ctl.versions_dir() / prev_tag)
    rollback_rc = service_ctl.start(
        target=rollback_spawn.argv, cwd=rollback_spawn.cwd, wait_ready=True
    )
    if rollback_rc == service_ctl.EXIT_OK:
        engine_update.finish_apply(tag=tag, result="rolled_back", trigger=trigger)
        if trigger == "auto":
            engine_update.record_auto_outcome(tag=tag, result="rolled_back")
        print(f"mnemo update-apply: rolled back to {prev_tag}, service is healthy")
        return EXIT_ERROR

    rollback_failed_error = f"rollback to {prev_tag} also failed health (rc={rollback_rc})"
    engine_update.finish_apply(
        tag=tag, result="failed", error=rollback_failed_error, trigger=trigger
    )
    if trigger == "auto":
        engine_update.record_auto_outcome(
            tag=tag, result="failed", error=rollback_failed_error
        )
    print("mnemo update-apply: ROLLBACK ALSO FAILED — service is down; "
          "check `mnemo service status` / `mnemo doctor`")
    return service_ctl.EXIT_DOWN


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


# `banks freeze|unfreeze|disable` -> the state each one sets. `unfreeze` is
# the way back from either dormant state, which is why it is not called
# `enable`: what a user wants named is the thing they are undoing.
_BANK_STATE_ACTIONS = {
    "freeze": "frozen",
    "unfreeze": "enabled",
    "disable": "disabled",
}

_STATE_SAID = {
    "frozen": "frozen — its index is held as it is, and still searchable",
    "enabled": "enabled — following its files again; catching up now",
    "disabled": "disabled — not watched, not searchable, still registered",
}


def _find_bank(c, ref: str) -> dict:
    """A bank from the API's own listing, by name or id. Exits 1 on a miss."""
    banks = {b["name"]: b for b in c.banks()}
    target = banks.get(ref) or next(
        (b for b in banks.values() if b["id"] == ref), None
    )
    if target is None:
        print(f"mnemo: no bank named {ref!r}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR)
    return target


def _cmd_banks(args: argparse.Namespace) -> int:
    # `path` is optional because `list` takes none. Every other action needs
    # it, and without this check they reported "no bank named None" — an
    # error about the wrong thing.
    if args.action != "list" and not args.path:
        need = ("the root folder to register" if args.action == "add"
                else "a bank name or id")
        print(f"mnemo: banks {args.action} needs {need}", file=sys.stderr)
        return EXIT_ERROR

    def call(c):
        if args.action == "list":
            banks = c.banks()
            if not banks:
                print("No banks registered.")
                return
            print(f"{'NAME':<20} {'STATE':<9} {'STATUS':<9} {'FILES':>6} "
                  f"{'CHUNKS':>7}  ROOT")
            for b in banks:
                # `state` is what the registry holds, `status` what the index
                # is doing. A pre-`state` backend omits the former.
                print(f"{b['name']:<20} {b.get('state', 'enabled'):<9} "
                      f"{b['status']:<9} {b['files']:>6} "
                      f"{b['chunks']:>7}  {b['root']}")
        elif args.action == "add":
            info = c.add_bank(str(Path(args.path).expanduser().resolve()),
                              name=args.name, provider=args.provider)
            print(f"registered {info['name']}  ({info['id']})  {info['root']}")
        elif args.action in _BANK_STATE_ACTIONS:
            want = _BANK_STATE_ACTIONS[args.action]
            target = _find_bank(c, args.path)
            if target.get("state") == want:
                print(f"{target['name']} is already {want}")
                return
            info = c.set_bank_state(target["id"], want)
            print(f"{info['name']} is now {_STATE_SAID[info['state']]}")
        else:
            target = _find_bank(c, args.path)
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
        # What "embed" means depends on the provider, so the word does too.
        # Under `local` it is the resident process, which is genuinely DOWN
        # when unreachable; under `api` nothing is probed — health() is
        # configuration-only by contract, so calling that state "DOWN" would
        # report a process that was never supposed to exist.
        if provider == "local":
            embed = ("reachable" if svc["embed"].get("reachable") else "DOWN")
        else:
            embed = ("configured" if svc["embed"].get("reachable")
                     else "not configured")
        print(f"provider {provider}"
              + (f" ({model}, dim {svc.get('provider_dim')})" if model else "")
              + f"  embed {embed}")
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

    # `status` reports what is, it does not bring the service up to answer —
    # unlike every other command here, which brings up a backend the user
    # should never have to know exists.
    return _run_api(call, autostart=False)


_HOLD_SAID = {
    "loaded": "loaded",
    "unloaded": "not loaded (returns on the next search)",
    # Each client phrases the steady states itself; the service sends `detail`
    # only for what it alone knows (an unreachable address, a mismatched
    # width), so these lines are not echoes of a field.
    "n/a": "nothing — this endpoint holds no model on this machine",
    "unknown": "unknown — could not ask the backend",
}


def _print_embed_state(info: dict) -> None:
    print(f"backend   {info.get('backend') or '—'}")
    print(f"model     {info.get('model') or '—'}")
    print(f"where     {info.get('where') or '—'}")
    held = info.get("holding")
    line = _HOLD_SAID.get(held, str(held))
    wake = info.get("wake_s")
    if held == "loaded" and wake:
        # The wake-up cost belongs next to "loaded", because it is the whole
        # argument for what unloading actually trades away.
        line += f" — unloading costs ~{wake:.0f}s on the next embed"
    print(f"holding   {line}")
    if info.get("expires_at"):
        print(f"expires   {info['expires_at']}")
    if info.get("cached") is not None:
        print(f"cached    {info['cached']}")
    if info.get("others_held"):
        # Named as a count, never by model: the others are somebody else's,
        # and this command neither lists nor touches them.
        print(f"note      {info['others_held']} other model(s) held there — "
              f"not ours, left alone")
    if info.get("probe_dim"):
        print(f"probe     ok, {info['probe_dim']}-wide vector")
    if info.get("detail"):
        print(f"          {info['detail']}")


def _cmd_embed(args: argparse.Namespace) -> int:
    action = getattr(args, "action", "status") or "status"

    def call(c):
        if action == "unload":
            info = c.embed_unload()
        elif action == "load":
            info = c.embed_load()
        else:
            info = c.embed_state()
        _print_embed_state(info)

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
    """Print the console's URL. Opens nothing.

    `/api` is open by default — no login token — since it is a loopback-only
    local channel (2026-08-21 decision, api.py's `auth_middleware`). So the
    plain URL is enough; nothing to fill in. If a token has been explicitly
    configured (`$MNEMO_API_TOKEN`, or a future opt-in "generate" step), it
    is still appended so the console stays reachable in that mode too.

    It used to call `webbrowser.open` as well. Which browser that reaches is
    not a decision this command gets to make: it is whatever the OS has
    registered, in whatever profile happens to be signed in. A printed line
    goes exactly where the user is already looking, and they click it, or
    paste it into the browser they meant.
    """
    client = _client()
    url = f"{client.base_url}/ui/"
    if client.token:
        url += f"?token={client.token}"
    print(url)
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
    wu = sub.add_parser(
        "warmup",
        help="Download the embedding model (~2.2 GB). The ONLY thing that "
             "does — no hook, service or search ever downloads it for you. "
             "Skips when nothing on this machine embeds locally.",
    )
    wu.add_argument(
        "--force", action="store_true",
        help="Cache the model even where no bank embeds locally — e.g. before "
             "switching back to `local`.",
    )
    sub.add_parser(
        "doctor",
        help="Answer 'why is memory not working': engine, venv, provider, "
             "model cache, embedding resident, token, backend, banks, orphan "
             "indexes. Reads only — it never changes anything.",
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
    # last hook entry point left, and it does nothing but exit 0. `update-
    # apply` is what the future `/api/update/apply` handler (step 9) spawns
    # detached to run the stop -> switch -> start -> health -> rollback
    # sequence outside the request/response cycle -- see the design topic
    # and _cmd_update_apply's own docstring.
    sub.add_parser("embed-server")
    sub.add_parser("hook-postedit")
    sub.add_parser("update-apply")

    pn = sub.add_parser(
        "init",
        help="Wire mnemo into a project: seed .claude/memory + the memory "
             "rule, register the bank, mint its token and write the MCP "
             "entry. Additive and idempotent; refuses rather than write a "
             "token into a git-tracked file, and writes NOTHING when it does.",
    )
    pn.add_argument(
        "--root", default=None,
        help="Project root. Default: $MNEMO_ROOT if set, else cwd.",
    )
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
    pv.add_argument("--port", type=int, default=None, help="Port (default: 4646).")

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
        "action", choices=("list", "add", "remove",
                           "freeze", "unfreeze", "disable"),
        help="list: name, state, files, chunks, root · add: register a "
             "folder of .md · remove: unregister it (the .md are never "
             "touched) · freeze: stop following the files, keep it "
             "searchable · unfreeze: follow them again and catch up · "
             "disable: switch it off entirely, keeping the registration.",
    )
    pb.add_argument("path", nargs="?", default=None,
                    help="add: the root folder; every other action: a name "
                         "or id.")
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
        help="Print the link to the local console — banks, file tree, chunk "
             "boundaries, reindex buttons, journal. Token filled in; opens "
             "no browser.",
    )

    pe = sub.add_parser(
        "embed",
        help="What the embedding backend is holding in memory, and give it "
             "back. Not an off switch — the model returns on the next search.",
    )
    pe.add_argument(
        "action", nargs="?", choices=("status", "unload", "load"),
        default="status",
        help="status: what is held right now (default) · unload: release it "
             "(~1.5 GB local, ~0.7 GB VRAM on Ollama) · load: bring it back "
             "with a probe embedding, which also proves the backend answers.",
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
        return _cmd_warmup(args)
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
    if cmd == "update-apply":
        return _cmd_update_apply()
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
    if cmd == "embed":
        return _cmd_embed(args)
    if cmd == "logs":
        return _cmd_logs(args)
    if cmd == "ingest":
        return _cmd_ingest(args)
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
