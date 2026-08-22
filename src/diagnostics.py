"""Structured machine diagnostics shared by the CLI and the console.

``mnemo doctor`` used to be a chain of ``print`` calls. That was enough for a
terminal and unusable for the console: parsing its prose would turn wording
into an API contract, while reimplementing the checks in ``api.py`` would give
one machine two doctors that could quietly disagree.

This module owns the facts. ``collect()`` returns plain JSON-shaped data with
no credentials; the CLI renders it as text and ``GET /api/doctor`` returns it
as data for the console. A caller supplies the backend facts it already knows:
the CLI probes the loopback endpoint, while the service describes itself and
never makes an HTTP request back into its own process.

Diagnostics are read-only. Orphan deletion is separate and explicit through
``delete_orphans()``. It accepts only ids that are still present in a freshly
read orphan list, then delegates to ``registry.delete_index()``, which reads
the registry yet again at the point of deletion. The two checks are deliberate:
a list shown in a browser is stale the moment another process registers a
bank, and stale evidence must never delete a live index.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import config, registry, settings


def human_bytes(size: int) -> str:
    """A compact binary size for both text and UI-facing reports."""
    value = float(size)
    for unit in ("B", "KiB", "MiB"):
        if value < 1024:
            return f"{value:.0f} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def _providers_in_use(banks: Iterable[registry.Bank]) -> tuple[str, list[str]]:
    """``(machine default, distinct per-bank overrides)``."""
    machine = settings.provider()
    overrides = sorted(
        {bank.provider for bank in banks if bank.provider and bank.provider != machine}
    )
    return machine, overrides


def _token_fact() -> dict[str, Any]:
    """Whether the CLI has a service credential, never the credential itself."""
    env = os.environ.get("MNEMO_API_TOKEN")
    if env and env.strip():
        return {
            "present": True,
            "source": "env",
            "where": "MNEMO_API_TOKEN",
            # A user-scope environment variable is visible across engine homes.
            # Naming that scope is what stops an isolated install from claiming
            # the token belongs to its own empty state directory.
            "scope": "machine",
        }
    path = Path(config.STATE_DIR) / "api.token"
    try:
        present = bool(path.read_text(encoding="utf-8").strip())
    except OSError:
        present = False
    return {
        "present": present,
        "source": "state_file",
        "where": path.as_posix(),
        "scope": "engine_home",
    }


def _self_update_fact() -> dict[str, Any]:
    """Self-update state (block M, ``state/engine_version.json``): the
    active tag, the last GitHub check, and whether a detached
    ``update-apply`` (step 8) looks stuck.

    "Stuck" means ``last_apply.started_at`` is set, ``finished_at`` is still
    null, and reasonably more time has passed than the apply orchestration
    could plausibly still be inside — i.e. the detached process died
    mid-flight (crashed, was killed, or hit an exception outside every path
    that writes a terminal state) and nothing else will ever report it,
    because the one process that could write "finished" no longer exists.
    The threshold is deliberately generous: a FAILED switch retries the
    whole stop -> start sequence again for rollback, so the worst realistic
    timeline is twice one attempt's stop+ready budget, plus a buffer for
    ``prune_versions()``/registry writes around the edges — not once, not
    a guess.
    """
    from . import engine_update

    state = engine_update.read_state()
    last_check = state.get("last_check") or {}
    last_apply = state.get("last_apply") or {}

    stuck: dict[str, Any] | None = None
    started_raw = last_apply.get("started_at")
    if started_raw and not last_apply.get("finished_at"):
        try:
            started = datetime.fromisoformat(started_raw)
            elapsed = (datetime.now(started.tzinfo) - started).total_seconds()
        except (ValueError, TypeError):
            elapsed = None
        threshold = 2 * (config.SERVICE_STOP_TIMEOUT + config.SERVICE_READY_TIMEOUT) + 30
        if elapsed is not None and elapsed > threshold:
            stuck = {
                "tag": last_apply.get("tag"),
                "started_at": started_raw,
                "elapsed_s": round(elapsed),
            }

    return {
        "current_tag": engine_update.effective_current_tag(state),
        "last_check_at": last_check.get("at"),
        "update_available": bool(last_check.get("update_available")),
        "latest_tag": last_check.get("latest_tag"),
        "stuck_apply": stuck,
    }


def probe_backend() -> dict[str, Any]:
    """Probe the loopback backend for the local CLI's doctor report.

    The endpoint is machine-scoped: changing ``MNEMO_HOME`` does not give an
    isolated test copy a second port. The report says so explicitly instead of
    placing a real machine's PIDs and bank count under the temporary home as if
    they belonged to it.
    """
    from .client import Client, ServiceDown

    client = Client(timeout=3.0, autostart=False)
    out: dict[str, Any] = {
        "up": False,
        "url": client.base_url,
        "scope": "machine_port",
        "error": None,
    }
    try:
        health = client.health()
    except ServiceDown as exc:
        out["error"] = str(exc)
        return out

    launcher = None
    try:
        from . import service_ctl

        launcher = (service_ctl.read_identity() or {}).get("pid")
    except Exception:  # noqa: BLE001 - diagnostics never fail on one row
        pass
    out.update(
        {
            "up": True,
            "serving_pid": health.get("pid"),
            "launcher_pid": launcher,
            "banks": health.get("banks"),
            "queue_depth": health.get("queue_depth"),
        }
    )
    return out


def _endpoint_fact(machine: str, overrides: list[str]) -> dict[str, Any]:
    """Configured API endpoint, without making a request to it."""
    if "api" not in {machine, *overrides}:
        return {"applicable": False}

    from .providers import get_provider

    try:
        provider = get_provider("api")
    except ValueError as exc:
        return {"applicable": True, "configured": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - one bad check is data
        return {
            "applicable": True,
            "configured": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "applicable": True,
        "configured": True,
        "url": settings.api_url(),
        "model": provider.model,
        "dim": provider.dim,
        "key_set": bool(settings.api_key()),
        "error": None,
    }


def orphan_json(orphan: registry.OrphanIndex) -> dict[str, Any]:
    """The complete safe-to-display form of one orphan index."""
    return {
        "id": orphan.id,
        "path": orphan.path.as_posix(),
        "size": orphan.size,
        "root": orphan.root,
        "root_exists": orphan.root_exists,
        "schema": orphan.schema,
        "files": orphan.files,
        "last_indexed": orphan.last_indexed,
        "error": orphan.error,
    }


def _project_wiring(banks: list[registry.Bank]) -> dict[str, Any]:
    """Projects whose mnemo wiring no longer matches this registry."""
    try:
        from .scaffold import adopted_projects

        projects = adopted_projects()
    except Exception as exc:  # noqa: BLE001 - diagnostics never fail wholesale
        return {"ok": False, "error": str(exc), "total": None, "stale": []}

    def covering(root: Path) -> registry.Bank | None:
        for bank in banks:
            try:
                if bank.root.is_relative_to(root):
                    return bank
            except (OSError, ValueError):
                continue
        return None

    def reason(project) -> str | None:
        if project.migrate:
            extra = (
                f" +{len(project.findings) - 1} more"
                if len(project.findings) > 1
                else ""
            )
            return f"{project.findings[0]}{extra}"
        bank = covering(project.root)
        if bank is None:
            return "no registered bank covers it"
        # Tokens are minted rather than derived. Compare internally, but never
        # put either value in the report that crosses the API boundary.
        if project.token and project.token != bank.token:
            return f"its token is not the one bank {bank.name!r} now carries"
        return None

    stale = []
    for project in projects:
        why = reason(project)
        if why is None:
            continue
        stale.append(
            {
                "root": project.root.as_posix(),
                "command": project.command(),
                "reason": why,
                "migrate": bool(project.migrate),
            }
        )
    return {"ok": True, "error": None, "total": len(projects), "stale": stale}


def collect(
    *,
    backend: dict[str, Any] | None = None,
    token: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect one complete, JSON-shaped doctor report.

    ``backend`` and ``token`` are injectable because the service already knows
    those facts. With neither supplied this is the local CLI path and the
    machine loopback endpoint is probed. No external embedding endpoint is ever
    called: an explicit «Перевірити» action owns that potentially metered
    request, not a diagnostic somebody may run repeatedly.
    """
    from .embed_server import server_is_up
    from .embedder import is_model_cached
    from .store import vector_support

    registry_error: str | None = None
    try:
        banks = registry.load()
    except Exception as exc:  # noqa: BLE001 - the error belongs in the report
        banks = []
        registry_error = str(exc)

    machine, overrides = _providers_in_use(banks)
    local_in_use = "local" in {machine, *overrides}
    model_cached = is_model_cached()
    unsupported = vector_support()

    if registry_error is None:
        try:
            orphan_items = registry.orphan_indexes()
            orphans = {
                "ok": True,
                "error": None,
                "count": len(orphan_items),
                "bytes": sum(item.size for item in orphan_items),
                "items": [orphan_json(item) for item in orphan_items],
            }
        except Exception as exc:  # noqa: BLE001
            orphans = {
                "ok": False,
                "error": str(exc),
                "count": None,
                "bytes": None,
                "items": [],
            }
    else:
        # Never ask for orphans after a registry failure: an empty-bank fallback
        # is exactly how every live index gets mislabeled as removable.
        orphans = {
            "ok": False,
            "error": registry_error,
            "count": None,
            "bytes": None,
            "items": [],
        }

    resident = {
        "applicable": local_in_use,
        "up": bool(server_is_up()) if local_in_use else None,
        "host": config.EMBED_HOST,
        "port": config.EMBED_PORT,
        "scope": "machine_port",
    }
    report = {
        "engine": {
            "home": Path(config.USER_HOME).as_posix(),
            "state_dir": Path(config.STATE_DIR).as_posix(),
            "python": Path(sys.executable).as_posix(),
        },
        "provider": {
            "machine": machine,
            "overrides": overrides,
            "local_in_use": local_in_use,
        },
        "model": {"cached": model_cached, "needed": local_in_use},
        "sqlite_vec": {"ok": unsupported is None, "error": unsupported},
        "resident": resident,
        "endpoint": _endpoint_fact(machine, overrides),
        "backend": dict(backend) if backend is not None else probe_backend(),
        "token": dict(token) if token is not None else _token_fact(),
        "registry": {
            "ok": registry_error is None,
            "error": registry_error,
            "count": len(banks) if registry_error is None else None,
            "banks": [
                {
                    "id": bank.id,
                    "name": bank.name,
                    "root": bank.root.as_posix(),
                    "state": bank.state,
                    "exists": bank.exists,
                }
                for bank in banks
            ],
        },
        "orphans": orphans,
        "wiring": _project_wiring(banks) if registry_error is None else {
            "ok": False,
            "error": registry_error,
            "total": None,
            "stale": [],
        },
        "self_update": _self_update_fact(),
    }
    return report


def _abbreviate(path: str) -> str:
    home = Path.home().as_posix()
    return f"~{path[len(home):]}" if path.startswith(home) else path


def orphan_line(orphan: dict[str, Any]) -> str:
    """One CLI line for an orphan JSON object."""
    if orphan.get("error"):
        where = f"(unreadable — {orphan['error']})"
    elif orphan.get("root"):
        where = _abbreviate(str(orphan["root"]))
        if orphan.get("root_exists"):
            where += "   [root still on disk]"
    elif orphan.get("files") is None:
        where = "(empty file — no index was ever written)"
    elif orphan.get("schema") is None:
        where = "(pre-v3 index — no root recorded)"
    else:
        where = "(no root recorded)"
    files = "?" if orphan.get("files") is None else str(orphan["files"])
    unit = "file " if orphan.get("files") == 1 else "files"
    return (
        f"  {orphan['id']}  {human_bytes(int(orphan.get('size') or 0)):>9}  "
        f"{files:>3} {unit}  {where}"
    )


def render_text(report: dict[str, Any]) -> str:
    """Render the structured report for ``mnemo doctor``."""
    lines: list[str] = []
    engine = report["engine"]
    lines.extend(
        [
            f"engine home      {engine['home']}",
            f"state dir        {engine['state_dir']}",
            f"python           {engine['python']}",
        ]
    )

    provider = report["provider"]
    overrides = provider.get("overrides") or []
    detail = f" (+ {', '.join(overrides)} on some banks)" if overrides else ""
    machine = provider.get("machine") or "unknown"
    lines.append(f"provider         {machine}{detail}")

    model = report["model"]
    cached = model.get("cached")
    if model.get("needed"):
        lines.append(f"model cached     {cached}")
    else:
        lines.append(f"model cached     {cached} — not needed under `{machine}`")

    vec = report["sqlite_vec"]
    lines.append(f"sqlite-vec       {'ok' if vec.get('ok') else 'UNAVAILABLE'}")
    if vec.get("error"):
        lines.append(f"                 {vec['error']}")

    resident = report["resident"]
    if resident.get("applicable"):
        state = "up" if resident.get("up") else "down (starts on first search)"
        lines.append(
            f"embed resident   {state} ({resident.get('host')}:{resident.get('port')}) "
            "[machine port]"
        )
    else:
        lines.append(f"embed resident   n/a under `{machine}`")

    endpoint = report["endpoint"]
    if endpoint.get("applicable"):
        if endpoint.get("configured"):
            lines.append(f"api endpoint     {endpoint.get('url')}")
            lines.append(
                f"                 model {endpoint.get('model')}, "
                f"dim {endpoint.get('dim')}"
                + (", key set" if endpoint.get("key_set") else ", no key")
            )
        else:
            lines.append(
                f"api endpoint     NOT CONFIGURED — {endpoint.get('error') or 'unknown'}"
            )

    backend = report["backend"]
    lines.append(f"backend url      {backend.get('url') or '—'} [machine port]")
    token = report["token"]
    if token.get("present"):
        lines.append(
            f"api token        set ({token.get('where') or token.get('source') or 'unknown'}; "
            f"{token.get('scope') or 'unknown scope'}) — /api requires it"
        )
    else:
        lines.append(
            "api token        not set — /api is open on loopback by default "
            "(/mcp, /mcp-admin, /mcp-tools still require their own token)"
        )
    if backend.get("up"):
        pids = f"serving pid {backend.get('serving_pid')}"
        launcher = backend.get("launcher_pid")
        if launcher and launcher != backend.get("serving_pid"):
            pids += f", launcher pid {launcher}"
        lines.append(
            f"backend          up ({pids}, {backend.get('banks')} banks, "
            f"queue {backend.get('queue_depth')}) [machine port]"
        )
    else:
        lines.append(f"backend          DOWN — {backend.get('error') or 'not reachable'}")

    reg = report["registry"]
    if not reg.get("ok"):
        lines.append(f"registry         UNREADABLE — {reg.get('error')}")
        return "\n".join(lines)

    banks = reg.get("banks") or []
    lines.append(f"banks            {len(banks)}")
    for bank in banks:
        flags = []
        if bank.get("state") != "enabled":
            flags.append(str(bank.get("state")))
        if not bank.get("exists"):
            flags.append("ROOT MISSING")
        suffix = f"  [{', '.join(flags)}]" if flags else ""
        lines.append(f"  {bank.get('name', ''):<20} {bank.get('root')}{suffix}")

    orphans = report["orphans"]
    if not orphans.get("ok"):
        lines.append(f"orphan indexes   UNKNOWN — {orphans.get('error')}")
    elif orphans.get("count"):
        lines.append(
            f"orphan indexes   {orphans['count']} ({human_bytes(orphans['bytes'])}) — "
            "run `mnemo clean-orphans`"
        )
    else:
        lines.append("orphan indexes   none")

    wiring = report["wiring"]
    if not wiring.get("ok"):
        lines.append(f"project wiring   UNKNOWN — {wiring.get('error')}")
    else:
        stale = wiring.get("stale") or []
        total = int(wiring.get("total") or 0)
        if not stale:
            lines.append(f"project wiring   {total} project(s), all current")
        else:
            lines.append(
                f"project wiring   {len(stale)} of {total} project(s) need rewiring"
            )
            for project in stale:
                lines.append(f"  {project['command']}")
                lines.append(f"      {project['reason']}")

    self_update = report.get("self_update") or {}
    current_tag = self_update.get("current_tag")
    if current_tag:
        detail = (
            f", {self_update['latest_tag']} available"
            if self_update.get("update_available") and self_update.get("latest_tag")
            else ""
        )
        lines.append(f"self-update      current {current_tag}{detail}")
    else:
        lines.append("self-update      no self-update recorded yet (plain install)")
    stuck = self_update.get("stuck_apply")
    if stuck:
        lines.append(
            f"                 STUCK APPLY — {stuck['tag']} started "
            f"{stuck['started_at']} ({stuck['elapsed_s']}s ago, no terminal "
            "state) — check `mnemo service status` / apply it again"
        )
    return "\n".join(lines)


class OrphanCleanupRefused(RuntimeError):
    """The current registry/orphan list is not safe enough to delete from."""


def delete_orphans(ids: Iterable[str]) -> dict[str, Any]:
    """Delete only requested ids that are still orphans right now."""
    requested = []
    seen = set()
    for raw in ids:
        index_id = str(raw or "").strip()
        if not index_id or index_id in seen:
            continue
        seen.add(index_id)
        requested.append(index_id)

    try:
        current = {item.id: item for item in registry.orphan_indexes()}
    except Exception as exc:  # noqa: BLE001 - refusal carries the reason
        raise OrphanCleanupRefused(
            f"cannot read a trustworthy orphan list: {exc}"
        ) from exc

    removed: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    locked: list[dict[str, Any]] = []
    freed = 0
    for index_id in requested:
        orphan = current.get(index_id)
        if orphan is None:
            skipped.append(
                {"id": index_id, "reason": "not in the current orphan list"}
            )
            continue
        try:
            count, failed = registry.delete_index(index_id)
        except (ValueError, OSError) as exc:
            skipped.append({"id": index_id, "reason": str(exc)})
            continue
        if failed:
            locked.append(
                {
                    "id": index_id,
                    "paths": [path.as_posix() for path in failed],
                }
            )
            continue
        removed.append({"id": index_id, "files_removed": count, "bytes": orphan.size})
        freed += orphan.size

    return {
        "requested": requested,
        "removed": removed,
        "skipped": skipped,
        "locked": locked,
        "freed_bytes": freed,
    }
