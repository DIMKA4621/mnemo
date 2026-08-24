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
import re
import shutil
import subprocess
import tarfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import config, service_ctl, settings

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

    ``"auto"`` is deliberately named apart from the ``"auto_update"``
    *setting* (``settings.py``'s ``state/settings.json``) — different files,
    to avoid the two being read as the same thing. This holds the pending-
    apply handoff and the per-tag blacklist (see :func:`set_pending_trigger`,
    :func:`record_auto_outcome`, :func:`auto_eligible_tag`); a state file
    predating this feature simply lacks the key, and every reader here
    treats that the same as an empty one rather than requiring it.
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
        "auto": {
            "pending_trigger": None,
            "blacklist": {},
        },
    }


def _detect_own_tag() -> str | None:
    """Best-effort: the name of this process's own ``versions/<tag>/``
    directory (e.g. ``"v3.0.1"``, or ``"local"`` for a dev build). ``None``
    outside the versioned layout (devserver, tests, a checkout run
    directly) -- there is no tag to detect there.

    Exists to close a real gap: a fresh install (``get.ps1``/``install.ps1``)
    builds ``versions/<tag>/`` straight from a release archive but never
    calls :func:`record_installed` -- confirmed by reading both installers,
    zero calls. So ``current`` stays ``None`` until the FIRST self-update
    ever runs, and every comparison against ``latest_tag`` until then reads
    as "update available", even for the version that was just installed.
    Self-detecting from the directory this code is actually running from
    closes that gap without requiring either installer to remember an extra
    step, and self-heals if the state file is ever missing, deleted, or
    hand-edited -- see :func:`effective_current_tag`.
    """
    try:
        this_file = Path(__file__).resolve()
        if this_file.parent.parent.parent.name == "versions":
            return this_file.parent.parent.name
    except Exception:  # noqa: BLE001 - best-effort only, never worth crashing over
        pass
    return None


def effective_current_tag(state: dict[str, Any]) -> str | None:
    """This process's own self-detected tag (see :func:`_detect_own_tag`)
    when available, else whatever the registry last recorded.

    Self-detection wins, not the registry, on purpose -- found live
    (2026-08-22, same day as the rest of this module): running a *local*
    `install.ps1` rebuild against a machine that had previously done a real
    self-update repoints `current` to `versions/local/` directly, without
    ever calling :func:`record_installed` (a local rebuild is not a
    self-update). `engine_version.json`'s `current` is left holding the
    OLD tag (e.g. "v3.0.1") while the engine actually running is "local" --
    reproduced live: `doctor`/`/api/update/status` kept reporting the old
    tag straight after `current -> local` printed. `state.get("current")`
    reflects "what the last recorded switch WAS", not "what is actually
    running right now" the moment anything reassigns `current` outside the
    self-update path -- and self-detection is ground truth for exactly that
    question, so it must win whenever it can answer at all. The registry
    stays the fallback for when self-detection genuinely cannot answer
    (devserver, tests, a checkout run directly, outside ~/.mnemo entirely).

    Use this everywhere "current" is compared against a known-latest tag.
    """
    return _detect_own_tag() or state.get("current")


_LOCAL_BUILD_SUFFIX_RE = re.compile(r"(\d)l$")


def base_version_tag(tag: str | None) -> str | None:
    """Strip a trailing lowercase "l" local-build marker (2026-08-22
    scheme, ``Get-LocalCheckoutVersionTag``/``get_local_checkout_version_tag``)
    off a tag like ``"v3.0.1l"``, giving ``"v3.0.1"`` -- the release it is
    actually based on. Identity on a tag with no such marker.

    Found live: comparing ``effective_current_tag()`` against ``latest_tag``
    literally means a local build sitting on TOP of the latest release
    (uncommitted fixes on a checkout at v3.0.1, tag "v3.0.1l") can never
    match "v3.0.1" by string equality, so it nags "update available"
    forever -- offering to overwrite those very fixes with the vanilla
    release. Comparing base tags instead answers the question this is
    actually asking: is there a release newer than what this build is
    based on, not "is this string byte-identical to a release tag."
    """
    if not tag:
        return tag
    return _LOCAL_BUILD_SUFFIX_RE.sub(r"\1", tag)


_SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _major_version(tag: str | None) -> int | None:
    """The leading ``MAJOR`` out of a ``vMAJOR.MINOR.PATCH`` tag, or ``None``
    for anything that does not match that exact shape (``"local"``, a
    trailing local-build ``l`` not yet stripped by :func:`base_version_tag`,
    a malformed tag from some future format change).

    ``None`` is the deliberate "I don't know" answer, not "block it" --
    :func:`auto_eligible_tag` treats it as permission to proceed, same as
    before this gate existed. Every tag this project mints today matches
    the pattern; this only exists for whatever does not.
    """
    if not tag:
        return None
    match = _SEMVER_RE.match(tag)
    return int(match.group(1)) if match else None


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


def start_apply(tag: str, *, trigger: str = "manual") -> dict[str, Any]:
    """Record that an apply attempt for ``tag`` has begun.

    ``trigger`` ("manual" or "auto") rides along on ``last_apply`` itself so
    a client reading ``/api/update/status`` after the fact — possibly a
    fresh process, post-restart — can tell the two apart (the frontend's own
    auto-close-on-success behaviour depends on it). Purely descriptive here;
    the blacklist-affecting decision still reads ``read_pending_trigger()``
    directly, unchanged.
    """
    state = read_state()
    state["last_apply"] = {
        "tag": tag,
        "trigger": trigger,
        "started_at": _now_iso(),
        "finished_at": None,
        "result": None,
        "error": None,
    }
    write_state(state)
    return state


def finish_apply(
    *, tag: str, result: str, error: str | None = None, trigger: str = "manual"
) -> dict[str, Any]:
    """Record how an apply attempt for ``tag`` ended."""
    state = read_state()
    state["last_apply"] = {
        "tag": tag,
        "trigger": trigger,
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
            "update_available": bool(latest_tag)
            and latest_tag != base_version_tag(effective_current_tag(state)),
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


# --------------------------------------------------------- auto-apply gate
#
# Unattended auto-apply (block M extension). This section owns only the
# STATE around it -- the pending-trigger handoff and the per-tag blacklist --
# and the single eligibility gate the background checker consults. The
# in-memory countdown, the WS event and the two confirm/cancel endpoints all
# live in api.py (they need a live process to hold a threading.Timer and a
# WS hub; this module has neither), and `update-apply`'s own orchestration
# is unchanged -- it just now consults :func:`read_pending_trigger` once and
# reports its outcome through :func:`record_auto_outcome` when that trigger
# was "auto".


def _iso_from_now(delta_seconds: float) -> str:
    return (
        datetime.now(timezone.utc).astimezone() + timedelta(seconds=delta_seconds)
    ).isoformat()


def set_pending_trigger(tag: str, trigger: str) -> None:
    """Record which path -- ``"auto"`` or ``"manual"`` -- is about to invoke
    `update-apply` for ``tag``.

    This is how the fact survives the process handoff: the staging thread
    that decides this (inside the long-lived API process) and the
    separately-spawned `update-apply` process that actually performs the
    switch and needs to attribute its outcome share no memory, only this
    file. Overwritten on every apply, auto or manual alike -- only the most
    recent attempt's origin matters.
    """
    state = read_state()
    auto = dict(state.get("auto") or default_state()["auto"])
    auto["pending_trigger"] = {"tag": tag, "trigger": trigger}
    state["auto"] = auto
    write_state(state)


def read_pending_trigger(tag: str) -> str:
    """``"auto"`` only when the recorded pending trigger's ``tag`` matches
    the argument, else ``"manual"``.

    Safe default: an apply of unknown or stale origin (nothing was ever
    recorded, or it names a different tag) is never silently treated as
    auto for blacklist bookkeeping.
    """
    state = read_state()
    pending = (state.get("auto") or {}).get("pending_trigger") or {}
    if pending.get("tag") == tag:
        return str(pending.get("trigger") or "manual")
    return "manual"


def record_auto_outcome(*, tag: str, result: str, error: str | None = None) -> dict[str, Any]:
    """Update the per-tag blacklist after an AUTO-triggered apply's outcome.

    Call only once the caller has confirmed
    ``read_pending_trigger(tag) == "auto"`` -- a pure staging failure (a
    network/download error, before `update-apply` is even spawned) must
    never reach this function, so it never burns an attempt. That path is
    reached from a different place entirely (``api._run_staged_apply``'s
    ``except`` clause), which never calls this.

    ``result`` matches the vocabulary `_cmd_update_apply` already passes to
    :func:`finish_apply`: ``"applied"`` / ``"rolled_back"`` / ``"failed"``
    (the latter covers both "no rollback target" and "rollback itself also
    failed health" -- both are genuine post-switch failures). ``error`` is
    whatever detail the caller already has at hand for that same outcome
    (the same string it passed, or would pass, to `finish_apply`'s own
    ``error`` argument); optional because a clean rollback carries none.

    * ``result == "applied"`` clears any existing blacklist entry for
      ``tag`` -- a successful retry forgives past failures.
    * Any other result is a genuine post-switch failure: ``attempts``
      increments (creating the record at 1 if none existed), and on
      reaching :data:`config.UPDATE_AUTO_APPLY_MAX_ATTEMPTS` the tag is
      permanently ``blacklisted`` (``next_retry_at`` cleared); below that
      threshold, a ``next_retry_at`` window opens
      :data:`config.UPDATE_AUTO_APPLY_RETRY_DELAY_S` out.
    """
    state = read_state()
    auto = dict(state.get("auto") or default_state()["auto"])
    blacklist = dict(auto.get("blacklist") or {})

    if result == "applied":
        blacklist.pop(tag, None)
    else:
        entry = dict(
            blacklist.get(tag)
            or {"attempts": 0, "blacklisted": False, "next_retry_at": None}
        )
        attempts = int(entry.get("attempts", 0)) + 1
        entry["attempts"] = attempts
        entry["last_error"] = error
        entry["last_failed_at"] = _now_iso()
        if attempts >= config.UPDATE_AUTO_APPLY_MAX_ATTEMPTS:
            entry["blacklisted"] = True
            entry["next_retry_at"] = None
        else:
            entry["blacklisted"] = False
            entry["next_retry_at"] = _iso_from_now(config.UPDATE_AUTO_APPLY_RETRY_DELAY_S)
        blacklist[tag] = entry

    auto["blacklist"] = blacklist
    state["auto"] = auto
    write_state(state)
    return state


def auto_eligible_tag() -> str | None:
    """The single gate the background checker consults every tick: is
    there a tag it may offer to auto-apply right now?

    ``None`` whenever: the ``auto_update`` machine setting is off, there is
    no known newer tag (or the last check does not say one is available),
    the tag is permanently blacklisted, or its retry window has not opened
    yet. Otherwise, the currently-known latest tag.
    A MAJOR version bump is never auto-applied, regardless of everything
    else above — a major release is where a breaking change is allowed to
    live, and an unattended machine should never cross that line on its
    own. Minor/patch bumps are unaffected. Decided live with the user after
    the console-window investigation: "мажорна версія оновлюється тільки
    вручну" (2026-08-22). Comparison is by parsed MAJOR, not string
    equality, so it survives a local-build ``l`` suffix on either side
    (:func:`base_version_tag` strips it first). Either side failing to
    parse as ``vMAJOR.MINOR.PATCH`` (:func:`_major_version` returning
    ``None``) falls back to the OLD behaviour — allow it — rather than
    blocking on a tag shape this project has never actually minted.
    """
    if not settings.auto_update_enabled():
        return None
    state = read_state()
    last_check = state.get("last_check") or {}
    tag = last_check.get("latest_tag")
    if not tag or not last_check.get("update_available"):
        return None
    current_major = _major_version(base_version_tag(effective_current_tag(state)))
    target_major = _major_version(base_version_tag(tag))
    if current_major is not None and target_major is not None and target_major > current_major:
        return None
    blacklist = (state.get("auto") or {}).get("blacklist") or {}
    entry = blacklist.get(tag)
    if entry:
        if entry.get("blacklisted"):
            return None
        next_retry_at = entry.get("next_retry_at")
        if next_retry_at:
            try:
                retry_at = datetime.fromisoformat(next_retry_at)
            except ValueError:
                retry_at = None
            if retry_at is not None and retry_at > datetime.now(timezone.utc).astimezone():
                return None
    return tag


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
                # Unattended auto-apply rides the same tick, deliberately --
                # see the section docstring above. maybe_begin_auto_apply()
                # only flips in-memory state and arms a short-lived
                # threading.Timer before returning; the multi-minute staging
                # work happens later, off this thread, when that timer fires
                # or a confirm arrives (api._run_staged_apply is already
                # invoked the same non-blocking way). A bug here must never
                # kill the checker thread, same tolerance as check_now above.
                try:
                    tag = auto_eligible_tag()
                    if tag:
                        from . import api  # noqa: PLC0415 - deferred: api.py imports this module

                        api.maybe_begin_auto_apply(tag)
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


@dataclass(frozen=True)
class DiskSpaceCheck:
    """One free-space snapshot for ``target``, against ``required_bytes``.

    A plain value object rather than a bare bool so a caller (``diagnostics``,
    the failure message below) can report *how much* is short, not just that
    it is.
    """

    target: Path
    required_bytes: int
    available_bytes: int

    @property
    def ok(self) -> bool:
        return self.available_bytes >= self.required_bytes


class InsufficientDiskSpace(RuntimeError):
    """Not enough free space for the download about to start.

    Shared between staging a release (:func:`stage_release`) and downloading
    the embedding model (``cli._cmd_warmup``) — the message names no
    particular caller, just the target directory and the shortfall.
    """

    def __init__(self, check: DiskSpaceCheck) -> None:
        from .diagnostics import human_bytes

        self.check = check
        super().__init__(
            f"not enough disk space at {check.target}: "
            f"need {human_bytes(check.required_bytes)}, "
            f"have {human_bytes(check.available_bytes)} free"
        )


def check_disk_space(
    *,
    model_cached: bool | None = None,
    include_version_size: bool = True,
    target: Path | None = None,
) -> DiskSpaceCheck:
    """Free space at ``target`` vs. what the caller's download will need.

    Two shapes share this one function:

    * **Staging a release** (the default: ``target=None`` ->
      ``versions/``, ``include_version_size=True``) — a built engine tree
      plus a buffer, plus the model download if it is not already cached.
      This is what :func:`stage_release` calls with no arguments.
    * **Downloading the model alone** (``cli._cmd_warmup``: ``target=
      config.MODEL_CACHE``, ``include_version_size=False``) — no new engine
      version is built, so its size never enters the budget; only the model
      download plus the buffer, checked against ``model-cache/`` instead of
      ``versions/``.

    ``model_cached=None`` (the staging default) looks it up itself via
    ``embedder.is_model_cached``. A caller that already computed it this same
    tick — ``diagnostics.collect`` fills its own ``report["model"]`` from the
    same check — passes it through instead, so the (cheap but not free) cache
    probe does not run twice for one ``doctor`` report. ``cli._cmd_warmup``
    passes ``model_cached=False`` unconditionally: warmup exists to (re)fetch
    the model, so the download always counts, ``--force`` included.

    The model download only counts toward ``required_bytes`` when it is not
    already cached: an already-warmed-up machine's self-update never touches
    ``model-cache/`` at all.
    """
    if model_cached is None:
        from .embedder import is_model_cached

        model_cached = is_model_cached()

    if target is None:
        target = service_ctl.versions_dir()

    required = config.INSTALL_DISK_BUFFER_BYTES
    if include_version_size:
        required += config.ENGINE_VERSION_SIZE_BYTES
    if not model_cached:
        required += config.MODEL_DOWNLOAD_SIZE_BYTES

    target.mkdir(parents=True, exist_ok=True)
    available = shutil.disk_usage(target).free
    return DiskSpaceCheck(target=target, required_bytes=required, available_bytes=available)


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


def _build_engine_version(
    repo_root: Path,
    version_dir: Path,
    *,
    timeout: float = 3600.0,
    progress_file: Path | None = None,
) -> None:
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

    ``timeout`` is a last-resort backstop only (bumped from the original
    1800s to a generous 3600s) — the real slow-vs-dead distinction now lives
    inside ``install.ps1``'s own ``Invoke-CheckedWithHeartbeat`` stall
    detector (``-StallTimeoutSec``), which kills a genuinely stalled pip
    install in ~2 minutes regardless of this ceiling. This one only fires if
    PowerShell itself gets wedged outside pip's control. ``progress_file``,
    when given, is forwarded as ``-ProgressFile`` so the pip-install step
    can report an approximate running status; see :func:`stage_release`
    for who reads it.
    """
    if os.name != "nt":
        raise NotImplementedError(
            "engine self-update staging is Windows-only for now "
            "(see the design topic's migration-risk decision)"
        )

    installer = repo_root / "install.ps1"
    if not installer.is_file():
        raise RuntimeError(f"release archive has no install.ps1 at {installer}")

    progress_arg = (
        f" -ProgressFile '{_ps_quote(progress_file)}'" if progress_file else ""
    )
    script = (
        f". '{_ps_quote(installer)}'; "
        "$py = Resolve-PythonCommand ''; "
        f"Build-EngineVersion -RepoRoot '{_ps_quote(repo_root)}' "
        f"-VersionDir '{_ps_quote(version_dir)}' -PythonCommand $py"
        f"{progress_arg} | Out-Null; "
        "Write-Output 'MNEMO_BUILD_OK'"
    )
    # This runs inside the windowless backend process (the apply handler's
    # background thread) — a bare `subprocess.run(["powershell", ...])` here
    # flashes a new visible console for the whole build (the exact "blank
    # blue window during self-update" bug found live: this was the one
    # `powershell` subprocess.run in the codebase missing the flag). Same
    # constant and same reasoning as `service_ctl._CREATE_NO_WINDOW` and
    # `scaffold.py`'s mcp-setup runner; harmless if a console already exists.
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **kwargs,
    )
    if completed.returncode != 0 or "MNEMO_BUILD_OK" not in completed.stdout:
        raise RuntimeError(
            f"Build-EngineVersion failed (exit {completed.returncode}): "
            f"{completed.stderr.strip() or completed.stdout.strip()}"
        )


def _pip_progress_detail(status: dict[str, Any]) -> str | None:
    """Turn install.ps1's ``{"phase","count","at"}`` status into the
    ``detail`` string shown in the console. ``count`` is a rough,
    approximate tally of ``Collecting`` lines seen (see
    ``Invoke-CheckedWithHeartbeat``'s own docstring on why this is never an
    exact "N of M") — worded accordingly, no denominator implied.
    """
    phase = status.get("phase")
    count = status.get("count") or 0
    if phase == "collecting":
        return f"встановлення пакетів… (побачено ~{count})"
    if phase in ("installing", "done"):
        return "завершення встановлення пакетів…"
    return None


def _watch_pip_progress(tag: str, progress_file: Path, stop_event: threading.Event) -> None:
    """Poll ``progress_file`` while :func:`_build_engine_version`'s blocking
    call runs, forwarding changes via :func:`_emit_progress`.

    Deliberately a side-channel file, not a streaming rewrite of
    ``_build_engine_version`` itself: that call stays a plain
    ``subprocess.run`` (scope decision — the concurrent-stdout-across-a-
    process-boundary rewrite was ruled out as bigger and riskier than this
    problem needs). This thread only ever reads a file install.ps1's own
    heartbeat writes best-effort; a missing or unreadable file (nothing
    written yet, or the caller passed none) just means no update this tick,
    never an error.

    **Bug found live (2026-08-22), fixed here:** the original shape was
    ``while not stop_event.wait(1.0): read...`` — a read only ever happened
    AFTER a full timeout elapsed with no stop signal. The moment
    ``stage_release`` calls ``stop_event.set()`` (right when the blocking
    build call returns), ``wait()`` returns ``True`` immediately and the
    loop exits WITHOUT reading — so any build that finished faster than one
    poll interval, or whose last progress write landed inside the final
    partial interval, reported no detail at all. Confirmed live: a real
    ``stage_release()`` run with a warm pip cache produced a bare
    ``{"step": "venv", "detail": None}``. Restructured so every iteration
    reads first and checks the stop signal after — the loop still exits
    promptly (``wait()`` still returns as soon as the event is set, it just
    no longer skips that iteration's read).
    """
    last_raw: str | None = None
    while True:
        stopped = stop_event.wait(0.5)
        try:
            # "utf-8-sig", not "utf-8": Windows PowerShell 5.1's
            # `Set-Content -Encoding utf8` always prepends a BOM (unlike
            # PowerShell 7+). Found live (2026-08-22): with plain "utf-8"
            # the leading U+FEFF landed inside the decoded string, so
            # every json.loads() below raised and was silently swallowed
            # by the `except ValueError` — the file was written correctly
            # the whole time, nothing ever got past this read.
            raw = progress_file.read_text(encoding="utf-8-sig")
        except OSError:
            raw = None
        if raw is not None and raw != last_raw:
            last_raw = raw
            try:
                status = json.loads(raw)
            except ValueError:
                status = None
            if status is not None:
                detail = _pip_progress_detail(status)
                if detail:
                    _emit_progress(tag, "venv", detail=detail)
        if stopped:
            return


def stage_release(
    tag: str,
    *,
    tarball_url: str | None = None,
    download_timeout: float | None = None,
    build_timeout: float = 3600.0,
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

    **Builds DIRECTLY into ``versions/<tag>/`` — no staging-dir-then-move.**
    An earlier version of this function built into
    ``state/tmp/update-<tag>/build/`` and moved the finished tree into place
    afterwards, on the theory that a stage+swap is the same "prepare beside,
    then rename in" shape ``service_ctl.switch_current()`` already uses for
    atomicity. **That was wrong, found live by tester (step 12, "Bug A"),
    confirmed by a byte-level read of a real built exe.** A venv's
    interpreter tolerates being moved; a pip-generated CONSOLE-SCRIPT exe
    (``mnemo.exe``, ``mnemow.exe``, ``pip.exe`` — anything from
    ``[project.scripts]``/``[project.gui-scripts]``) does not: pip bakes an
    ABSOLUTE shebang path into it at build time
    (``#!<VersionDir>\\.venv\\Scripts\\python.exe``), and after the move
    that path no longer exists — the exe fails immediately, no stdout, no
    stderr. The backend itself was never affected (``target_for_version()``/
    ``windowless_python()`` spawn ``pythonw.exe`` directly, never through
    these exe), but ``mnemo.exe`` is exactly what the human CLI, the
    PowerShell profile function and Task Scheduler autostart all resolve
    through — every successful self-update was silently breaking the `mnemo`
    command.

    Atomicity does not need the move at all: "ready to apply" was ALREADY
    defined (step 7/8's own contract) as "``versions/<tag>/VERSION`` exists
    and matches the tag" — a filesystem marker, not "the directory exists".
    A build that dies partway simply leaves ``versions/<tag>/`` without that
    marker, exactly as an incomplete stage always looked from the outside;
    the ``finally`` below removes the half-built tree so it never lingers
    looking like a real (if broken) release.

    Only the download and extraction still happen under
    ``state/tmp/update-<tag>/`` (per the design) — those produce no
    absolute-path artifacts, so there is nothing wrong with staging them;
    that directory is always removed in ``finally``, success or failure
    alike ("не лишити напіврозпаковану теку").

    Emits ``update_progress`` (§9.7) at each step: ``download``, ``venv``,
    then ``done`` or ``failed`` — plus zero or more extra ``venv`` events
    with an approximate ``detail`` while pip install runs (see
    :func:`_watch_pip_progress`). Raises on any failure — the caller (the
    apply handler) decides what a failed stage means for the running
    service; this function's only side effect on failure is that nothing
    usable exists under ``versions/<tag>/`` (no ``VERSION`` marker, and the
    directory itself is removed).
    """
    url = tarball_url or _tarball_url(tag)
    staging_root = service_ctl.state_dir() / "tmp" / f"update-{tag}"
    archive = staging_root / "download.tar.gz"
    extract_dir = staging_root / "extract"
    final_dir = service_ctl.versions_dir() / tag

    if staging_root.exists():
        shutil.rmtree(staging_root)  # leftover from a previous, failed attempt
    staging_root.mkdir(parents=True, exist_ok=True)

    built = False
    try:
        space = check_disk_space()
        if not space.ok:
            raise InsufficientDiskSpace(space)

        _emit_progress(tag, "download", detail=url)
        _download(url, archive, timeout=download_timeout)

        repo_root = _extract_tarball(archive, extract_dir)

        _emit_progress(tag, "venv")
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            shutil.rmtree(final_dir)  # a stale, incomplete stage of this same tag

        # Best-effort live detail for the "venv" step: _build_engine_version
        # stays a plain blocking call (see _watch_pip_progress's own
        # docstring for why), so a side-channel file + a polling thread is
        # what surfaces progress while it runs, instead of nothing at all
        # between the one _emit_progress above and the call returning.
        progress_file = staging_root / "pip-progress.json"
        stop_watch = threading.Event()
        watcher = threading.Thread(
            target=_watch_pip_progress, args=(tag, progress_file, stop_watch), daemon=True
        )
        watcher.start()
        try:
            _build_engine_version(
                repo_root, final_dir, timeout=build_timeout, progress_file=progress_file
            )
        finally:
            stop_watch.set()
            watcher.join(timeout=2.0)

        (final_dir / "VERSION").write_text(tag, encoding="utf-8")
        built = True

        _emit_progress(tag, "done", detail=str(final_dir))
        return final_dir
    except Exception as exc:  # noqa: BLE001 - reported, then re-raised
        _emit_progress(tag, "failed", error=str(exc))
        raise
    finally:
        with suppress(OSError):
            shutil.rmtree(staging_root, ignore_errors=True)
        if not built:
            with suppress(OSError):
                shutil.rmtree(final_dir, ignore_errors=True)
