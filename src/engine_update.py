"""Engine self-update — state format, GitHub check, staging (block M).

Steps 5-7 of the plan in ``.claude/memory/topics/engine-self-update-design.md``
(read it before touching this file — full design, UX flow and risk decisions
live there, not here). This module owns three things, kept in one file
because they form one small pipeline, each stage feeding the next:

1. **State** (step 5) — ``state/engine_version.json``: what is installed, the
   last release check, the last apply attempt. Round-tripped atomically
   (tmp+replace, same pattern as ``service_ctl._write_identity``), and
   tolerant of a missing or corrupt file — a broken state file must never
   crash the backend, only reset to :func:`default_state`.
2. **Check** (step 6) — one unauthenticated GET against GitHub's
   ``releases/latest``, plus a background timer that repeats it. Network
   failure is a *soft* failure: the error is recorded, the previously known
   ``latest_tag``/``update_available`` are left untouched.
3. **Stage** (step 7) — download a release tarball, extract it, and build a
   full ``{src, .venv}`` tree under ``versions/<tag>/`` by calling
   ``install.ps1``'s own ``Build-EngineVersion`` (see :func:`_build_engine_version`
   for why that is the extracted release's *own* copy, not the currently
   running engine's). Staging never touches ``current`` and never stops the
   service — the version it builds is inert until something else (the apply
   handler, step 8/9, NOT this file) calls ``service_ctl.switch_current``.

Explicitly NOT here, by design (see the task that produced this module):
``/api/update/*`` endpoints (step 9), the `update-apply` CLI (step 8,
platform-dev), and the stop -> repoint -> start -> health-gate -> rollback
orchestration around a switch. This module only produces a fully-built,
ready-to-switch-to version directory and a state file describing it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import threading
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import config, service_ctl

STATUS_ACTIVE = "active"
STATUS_PREVIOUS = "previous"
STATUS_FAILED = "failed"
_STATUSES = frozenset({STATUS_ACTIVE, STATUS_PREVIOUS, STATUS_FAILED})


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


# ------------------------------------------------------------------ state


def version_state_file() -> Path:
    """``state/engine_version.json`` — derived live, not cached.

    Same reasoning as every other live accessor in this codebase
    (``service_ctl.state_dir()``, ``registry.banks_file()``): a constant
    computed at import time would freeze whatever ``config.STATE_DIR`` was
    at that moment, and a test (or a container) that repoints it afterwards
    would silently keep reading/writing the old directory.
    """
    return service_ctl.state_dir() / "engine_version.json"


def default_state() -> dict[str, Any]:
    """The shape when nothing has ever been recorded — also the recovery
    target for a missing or corrupt state file (see :func:`read_state`).
    """
    return {
        "current": None,
        "installed": [],
        "last_check": {
            "at": None,
            "latest_tag": None,
            "update_available": False,
            "error": None,
        },
        "last_apply": {
            "tag": None,
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        },
    }


def _is_valid(data: Any) -> bool:
    """Structural sanity check, not a full schema validator.

    Enough to catch "this is not our JSON at all" (truncated write, a
    stray file, an old/foreign format) without being brittle about which
    exact keys a forward-compatible reader tolerates.
    """
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("installed"), list):
        return False
    if not isinstance(data.get("last_check"), dict):
        return False
    if not isinstance(data.get("last_apply"), dict):
        return False
    current = data.get("current")
    return current is None or isinstance(current, str)


def read_state() -> dict[str, Any]:
    """Read the state file, tolerant of "does not exist" and "is garbage".

    Never raises. A missing file (first run, or a machine that has never
    self-updated) and a corrupt one (crash mid-write despite the atomic
    replace below, hand-edited into invalid JSON, a foreign file) both fall
    back to :func:`default_state` — the same "recognised or default"
    contract the rest of this codebase's state files use (e.g.
    ``service_ctl.read_identity`` returning ``None`` on either failure mode).
    """
    path = version_state_file()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return default_state()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return default_state()
    return data if _is_valid(data) else default_state()


def write_state(state: dict[str, Any]) -> None:
    """Atomic tmp+replace — same technique as ``service_ctl._write_identity``,
    so a crash mid-write leaves either the old file or the new one, never a
    half-written one a subsequent :func:`read_state` would have to recover
    from.
    """
    path = version_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def record_installed(*, tag: str, commit: str | None, status: str) -> dict[str, Any]:
    """Add or replace one ``installed`` entry, keeping ``current`` in sync.

    Not orchestration — it does not stop, start or switch anything. It is
    the state-format half of "a switch happened", called by whatever DOES
    the switching (out of this module's scope). ``status="active"`` demotes
    any previously-active entry to ``"previous"`` and updates ``current``;
    any other status leaves ``current`` untouched (e.g. recording a staged-
    but-not-yet-switched-to, or a failed, attempt).

    GitHub's auto-generated release archive carries no ``.git`` directory,
    so ``commit`` has no source inside :func:`stage_release` itself — a
    caller that wants it has to resolve it from the release API separately
    (step 8/9). ``None`` is a valid, expected value here.
    """
    if status not in _STATUSES:
        raise ValueError(f"unknown status: {status!r}")
    state = read_state()
    installed = [e for e in state.get("installed", []) if e.get("tag") != tag]
    if status == STATUS_ACTIVE:
        for entry in installed:
            if entry.get("status") == STATUS_ACTIVE:
                entry["status"] = STATUS_PREVIOUS
        state["current"] = tag
        # `last_check.update_available` is computed once, at check time,
        # against whatever `current` was THEN — a switch changes `current`
        # without ever re-running a check, so it goes stale the instant an
        # apply succeeds unless it is re-derived right here. Confirmed live
        # (ui-dev, step 11): after a real apply, `current.tag ==
        # latest_known.tag` but `update_available` stayed `true` forever,
        # since nothing re-ran the background check. Same formula
        # `record_check()` uses — this just fires on the OTHER event that
        # can flip it: a new tag becoming current, not a new tag being
        # reported.
        last_check = dict(state.get("last_check") or {})
        last_check["update_available"] = (
            bool(last_check.get("latest_tag")) and last_check.get("latest_tag") != tag
        )
        state["last_check"] = last_check
    installed.append(
        {"tag": tag, "installed_at": _now_iso(), "commit": commit, "status": status}
    )
    state["installed"] = installed
    write_state(state)
    return state


def start_apply(tag: str) -> dict[str, Any]:
    """Record that an apply attempt for ``tag`` has begun."""
    state = read_state()
    state["last_apply"] = {
        "tag": tag,
        "started_at": _now_iso(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    write_state(state)
    return state


def finish_apply(*, tag: str, result: str, error: str | None = None) -> dict[str, Any]:
    """Record how an apply attempt for ``tag`` ended."""
    state = read_state()
    state["last_apply"] = {
        "tag": tag,
        "started_at": (state.get("last_apply") or {}).get("started_at"),
        "finished_at": _now_iso(),
        "result": result,
        "error": error,
    }
    write_state(state)
    return state


# ------------------------------------------------------------------ check


def check_latest_release(
    *, timeout: float | None = None, url: str | None = None
) -> tuple[str | None, str | None]:
    """One GET against GitHub's public ``releases/latest``. No token needed.

    Returns ``(tag, error)``. Every failure mode — unreachable host, DNS
    failure, connect/read timeout, a non-2xx status (404 = "repo has no
    releases yet", which this treats identically to any other failure:
    there is nothing to update to), malformed JSON, a response missing
    ``tag_name`` — comes back as ``(None, "<message>")``. Nothing here
    raises; :func:`record_check` is what turns that into the soft-failure
    semantics the design calls for.

    ``url`` overrides the derived GitHub endpoint — production code never
    passes it; it exists so a test can point this at an unreachable address
    to exercise the network-error path deterministically, without depending
    on the real internet being down.
    """
    import httpx

    url = url or f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
    budget = config.UPDATE_CHECK_TIMEOUT_S if timeout is None else timeout
    try:
        resp = httpx.get(
            url,
            timeout=budget,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "mnemo-self-update",
            },
        )
    except httpx.HTTPError as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if resp.status_code != 200:
        return None, f"GitHub API returned {resp.status_code}"
    try:
        data = resp.json()
    except ValueError:
        return None, "GitHub API returned a non-JSON response"
    tag = data.get("tag_name") if isinstance(data, dict) else None
    if not tag:
        return None, "GitHub API response has no tag_name"
    return tag, None


def record_check(*, latest_tag: str | None, error: str | None) -> dict[str, Any]:
    """Persist one check's outcome into ``last_check``.

    Soft-failure semantics (design topic, "Мережа недоступна чи GitHub
    повернув помилку"): on error, ``at`` still moves (a caller can see we
    tried) and ``error`` is set, but ``latest_tag``/``update_available``
    are copied forward unchanged — a network hiccup must never silently
    flip "update available" back to False, and must never erase the last
    known-good tag.
    """
    state = read_state()
    prev = state.get("last_check") or {}
    if error is None:
        state["last_check"] = {
            "at": _now_iso(),
            "latest_tag": latest_tag,
            "update_available": bool(latest_tag) and latest_tag != state.get("current"),
            "error": None,
        }
    else:
        state["last_check"] = {
            "at": _now_iso(),
            "latest_tag": prev.get("latest_tag"),
            "update_available": bool(prev.get("update_available", False)),
            "error": error,
        }
    write_state(state)
    return state


_check_lock = threading.Lock()


def check_in_progress() -> bool:
    """Whether a check is running right now — the ``check.in_progress``
    field of ``GET /api/update/status`` (step 9). Backed by the same lock
    :func:`check_now` holds for its whole body, so a concurrent GET during
    an in-flight ``POST /api/update/check`` (or a background-timer tick)
    reports the truth instead of always ``False``.
    """
    return _check_lock.locked()


def check_now(*, timeout: float | None = None) -> dict[str, Any]:
    """One check, persisted. The single entry point both the background
    timer (:func:`start_checker`) and ``POST /api/update/check`` (step 9)
    call — so the two can never disagree about what a check means.
    """
    with _check_lock:
        tag, error = check_latest_release(timeout=timeout)
        return record_check(latest_tag=tag, error=error)


# --------------------------------------------------------- background timer

_checker: threading.Thread | None = None
_checker_stop = threading.Event()
_checker_lock = threading.Lock()


def start_checker(interval_s: float | None = None) -> None:
    """Check once, then keep checking in the background.

    Pattern choice: this mirrors ``servicelog.start_pruner``'s thread shape
    (a daemon ``threading.Thread`` running a ``while not stop.wait(every)``
    loop) rather than an ``asyncio`` task alongside ``api.py``'s
    ``_ping_loop`` — two established patterns already coexist in this
    codebase (``watcher.py``'s rescan loop is the same threading shape, so
    it is two-to-one, not a coin flip) and a plain synchronous ``httpx.get``
    inside a thread needs nothing else to change; wiring the same call
    through the event loop would mean either blocking it (defeats the
    point) or introducing an async HTTP client used nowhere else here.

    One deliberate deviation from ``start_pruner``, called out because it
    is not obvious from the shape alone: ``start_pruner`` runs its first
    prune *synchronously, on the caller's thread*, before returning — a
    local SQLite prune costs milliseconds. A GitHub call can legitimately
    take seconds, or the full ``UPDATE_CHECK_TIMEOUT_S`` on a slow or
    offline link, and backend startup must never block on the outside
    world (the same "the backend does not block" principle the design
    topic states for staging). So the first check here happens *inside*
    the thread, not before this function returns.

    ``interval_s <= 0`` (default: ``config.UPDATE_CHECK_INTERVAL_S``, itself
    settable to 0) disables the timer entirely — no thread is started. A
    manual check still works via :func:`check_now` directly.
    """
    global _checker
    with _checker_lock:
        if _checker is not None and _checker.is_alive():
            return
        every = config.UPDATE_CHECK_INTERVAL_S if interval_s is None else float(interval_s)
        if every <= 0:
            return
        _checker_stop.clear()

        def _loop() -> None:
            while True:
                try:
                    check_now()
                except Exception:  # noqa: BLE001 - never kill the thread
                    pass
                if _checker_stop.wait(every):
                    return

        _checker = threading.Thread(
            target=_loop, name="mnemo-update-checker", daemon=True
        )
        _checker.start()


def stop_checker() -> None:
    global _checker
    _checker_stop.set()
    thread = _checker
    if thread is not None:
        thread.join(timeout=2.0)
    _checker = None


# -------------------------------------------------------------- progress

# In-process observers of update_progress, alongside the WS broadcast below.
# Step 9's ``POST /api/update/apply`` handler needs to mirror the live step
# into its own ``apply.step`` (for ``GET /api/update/status`` polling)
# without reaching into ``api.hub`` itself — this is the seam that lets it
# do that without engine_update knowing anything about api.py's request
# handling. A plain list, not a WS-shaped abstraction: nothing here assumes
# the listener is a socket.
_progress_listeners: list[Callable[[dict[str, Any]], None]] = []


def add_progress_listener(fn: Callable[[dict[str, Any]], None]) -> None:
    _progress_listeners.append(fn)


def remove_progress_listener(fn: Callable[[dict[str, Any]], None]) -> None:
    with suppress(ValueError):
        _progress_listeners.remove(fn)


def _emit_progress(
    tag: str, step: str, *, detail: str | None = None, error: str | None = None
) -> None:
    """Broadcast an ``update_progress`` event over the existing WS Hub
    (contracts §9.7): ``{"step","tag","detail","error"}``, ``bank_id=None``
    (a machine-level event, same convention every other service-wide event
    in ``api.py`` already uses) — and hand the same payload to every
    in-process listener registered via :func:`add_progress_listener`.

    No new WS channel — this reuses ``api.hub.broadcast`` via
    ``hub.publish``, exactly as instructed. The import is deferred rather
    than at module load: ``api.py`` imports this module (to wire
    :func:`start_checker`/:func:`stop_checker` into its lifespan), so an
    eager import here would be circular. A caller with no running API
    process (a standalone script, a test) silently has nowhere to
    broadcast to — swallowed, the same as every other best-effort
    ``hub.publish`` call already is in ``api.py`` itself.
    """
    payload = {"step": step, "tag": tag, "detail": detail, "error": error}
    with suppress(Exception):
        from . import api  # noqa: PLC0415

        api.hub.publish("update_progress", payload, None)
    for listener in list(_progress_listeners):
        with suppress(Exception):
            listener(payload)


# --------------------------------------------------------------- staging


def _tarball_url(tag: str) -> str:
    """GitHub's source archive for a tag — no API call needed to derive it.

    Deterministic from the tag name alone (confirmed against a real
    ``codeload.github.com`` request, see the step-6/7 verification): the
    release API's own ``tarball_url`` field would work too, but storing or
    threading it through would be one more piece of state to keep in sync
    with nothing gained.

    ``config.UPDATE_TARBALL_URL_TEMPLATE`` overrides this with a mirror —
    see its own docstring for why that knob exists (there is no real
    GitHub release of this in-progress feature to test the full
    ``POST /api/update/apply`` pipeline against, so step 9's live
    verification points it at a local server; the same knob generalises to
    an air-gapped machine or one behind a proxy that blocks GitHub).
    """
    template = config.UPDATE_TARBALL_URL_TEMPLATE
    if template:
        return template.format(tag=tag)
    return f"https://codeload.github.com/{config.GITHUB_REPO}/tar.gz/refs/tags/{tag}"


def _download(url: str, dest: Path, *, timeout: float | None = None) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=timeout or 60.0
    ) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)


def _safe_members(tar: tarfile.TarFile, dest: Path) -> list[tarfile.TarInfo]:
    """Reject any archive member that would land outside ``dest``.

    Defence in depth for a downloaded archive (the classic tar path-
    traversal class, CVE-2007-4559): the design's "cíлись на TLS, без
    SHA-звірки" decision is about *authenticity* of the bytes GitHub sent,
    not about trusting every path inside them blindly. Doing this by hand
    rather than ``tarfile.extractall(filter="data")`` because that
    parameter is Python 3.12+ only and this engine's floor is 3.10.
    """
    resolved_dest = dest.resolve()
    safe: list[tarfile.TarInfo] = []
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        try:
            target.relative_to(resolved_dest)
        except ValueError:
            raise RuntimeError(
                f"refusing to extract unsafe path from release archive: {member.name}"
            ) from None
        if member.issym() or member.islnk():
            link_target = ((dest / member.name).parent / member.linkname).resolve()
            try:
                link_target.relative_to(resolved_dest)
            except ValueError:
                raise RuntimeError(
                    "refusing to extract unsafe link from release archive: "
                    f"{member.name} -> {member.linkname}"
                ) from None
        safe.append(member)
    return safe


def _extract_tarball(archive: Path, dest: Path) -> Path:
    """Extract ``archive`` into ``dest``, returning the one directory it
    contains.

    GitHub's auto-generated tag/branch archives always wrap the tree in
    exactly one ``<owner>-<repo>-<sha>``-style top-level folder — confirmed
    against a real download in the step-7 verification.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, mode="r:gz") as tar:
        members = _safe_members(tar, dest)
        tar.extractall(dest, members=members)  # noqa: S202 - members pre-filtered above
    entries = [p for p in dest.iterdir() if p.is_dir()]
    if len(entries) != 1:
        raise RuntimeError(
            "expected exactly one top-level directory in the release archive, "
            f"found {len(entries)}: {[p.name for p in entries]}"
        )
    return entries[0]


def _ps_quote(path: Path) -> str:
    """Escape a path for embedding in a PowerShell single-quoted string."""
    return str(path).replace("'", "''")


def _build_engine_version(repo_root: Path, version_dir: Path, *, timeout: float = 1800.0) -> None:
    """Build a full ``{src, .venv}`` tree at ``version_dir`` from
    ``repo_root``'s source, by dot-sourcing THAT checkout's own
    ``install.ps1`` and calling its ``Build-EngineVersion`` function
    directly — never a Python re-implementation of what it does.

    **Coordination note, resolved before writing this (not decided here on
    the fly).** Whether ``Build-EngineVersion`` was already callable from
    outside ``install.ps1``, or whether platform-dev's file needed a new
    entry point, was an open question going in. Its own docstring in
    ``install.ps1`` answers it: the function is explicitly written for two
    callers, "the first full install" and "later, the self-update apply
    handler (step 7, service-dev, not this file) stages a new release tag
    the same way", via dot-sourcing — "the same reuse mechanism
    ``test_platform.py`` already relies on to exercise other functions here
    in isolation". So no change to ``install.ps1`` was needed, and none was
    made.

    Deliberately the EXTRACTED RELEASE's own ``install.ps1``, not the
    currently-running engine's: the release tarball is a full repo snapshot
    (GitHub's auto-archive), so it carries whatever ``Build-EngineVersion``
    looked like AT THAT TAG — a future change to how the venv gets built
    ships with the release that needs it, rather than requiring the OLD
    engine's installer to already know about it.

    Windows only. The design topic's own "Рішення по ризиках" scopes the
    whole self-update feature to the one machine it exists for; ``install.sh``
    never grew a reusable ``Build-EngineVersion`` equivalent (its venv build
    is inline), so there is nothing to call on POSIX yet.
    """
    if os.name != "nt":
        raise NotImplementedError(
            "engine self-update staging is Windows-only for now "
            "(see the design topic's migration-risk decision)"
        )

    installer = repo_root / "install.ps1"
    if not installer.is_file():
        raise RuntimeError(f"release archive has no install.ps1 at {installer}")

    script = (
        f". '{_ps_quote(installer)}'; "
        "$py = Resolve-PythonCommand ''; "
        f"Build-EngineVersion -RepoRoot '{_ps_quote(repo_root)}' "
        f"-VersionDir '{_ps_quote(version_dir)}' -PythonCommand $py | Out-Null; "
        "Write-Output 'MNEMO_BUILD_OK'"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0 or "MNEMO_BUILD_OK" not in completed.stdout:
        raise RuntimeError(
            f"Build-EngineVersion failed (exit {completed.returncode}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def _finalize_version_dir(build_dir: Path, final_dir: Path) -> None:
    """Move a fully-built version tree into place.

    Same "prepare beside, then swap" shape as
    ``service_ctl.switch_current()``: a stale ``final_dir`` (leftover from a
    previous, incomplete stage of the same tag) is removed first, then the
    freshly-built tree is renamed in. ``os.replace`` is atomic when staging
    and ``versions/`` share a filesystem — the normal case, both live under
    ``USER_HOME``. ``shutil.move`` is the fallback for ``MNEMO_STATE_DIR``
    pointed at a different volume than ``USER_HOME`` (see ``config.STATE_DIR``'s
    own container-relocation docstring) — a plain rename cannot cross
    filesystems at all, and this module's staging area is deliberately
    ``state/tmp/...`` per the design (relocatable), while ``versions/`` is
    not.
    """
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        shutil.rmtree(final_dir)
    try:
        os.replace(build_dir, final_dir)
    except OSError:
        shutil.move(str(build_dir), str(final_dir))


def stage_release(
    tag: str,
    *,
    tarball_url: str | None = None,
    download_timeout: float | None = None,
    build_timeout: float = 1800.0,
) -> Path:
    """Download, extract and build a venv for ``tag`` under
    ``versions/<tag>/`` — the whole of step 7, and nothing past it.

    Never touches ``current`` and never stops the service: the old version
    keeps serving for the entire duration (design topic, point 4 of the UX
    flow) because nothing here does anything but build a new, inert
    directory next to it. Switching to it is the apply handler's job
    (step 8/9, explicitly out of scope for this module).

    Meant to be called from a background thread — it is a plain blocking
    function (network I/O, then a ``subprocess.run`` that can take minutes),
    never from the event loop.

    Staging happens under ``state/tmp/update-<tag>/`` first (per the
    design), never directly under ``versions/``, so a crash or a failed
    build can never leave a half-built tree where ``switch_current()`` would
    look for a real one. That staging directory — the downloaded tarball and
    the extracted tree — is always removed in ``finally``, success or
    failure alike ("не лишити напіврозпаковану теку"); only a fully built
    tree ever reaches ``versions/<tag>/``.

    Emits ``update_progress`` (§9.7) at each step: ``download``, ``venv``,
    then ``done`` or ``failed``. Raises on any failure — the caller (the
    apply handler) decides what a failed stage means for the running
    service; this function's only side effect on failure is that nothing
    new exists under ``versions/``.
    """
    url = tarball_url or _tarball_url(tag)
    staging_root = service_ctl.state_dir() / "tmp" / f"update-{tag}"
    archive = staging_root / "download.tar.gz"
    extract_dir = staging_root / "extract"
    build_dir = staging_root / "build"
    final_dir = service_ctl.versions_dir() / tag

    if staging_root.exists():
        shutil.rmtree(staging_root)  # leftover from a previous, failed attempt
    staging_root.mkdir(parents=True, exist_ok=True)

    try:
        _emit_progress(tag, "download", detail=url)
        _download(url, archive, timeout=download_timeout)

        repo_root = _extract_tarball(archive, extract_dir)

        _emit_progress(tag, "venv")
        _build_engine_version(repo_root, build_dir, timeout=build_timeout)

        (build_dir / "VERSION").write_text(tag, encoding="utf-8")

        _finalize_version_dir(build_dir, final_dir)

        _emit_progress(tag, "done", detail=str(final_dir))
        return final_dir
    except Exception as exc:  # noqa: BLE001 - reported, then re-raised
        _emit_progress(tag, "failed", error=str(exc))
        raise
    finally:
        with suppress(OSError):
            shutil.rmtree(staging_root, ignore_errors=True)
