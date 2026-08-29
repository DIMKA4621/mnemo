"""The backend: one loopback HTTP service, one writer, many thin faces.

Everything mnemo does from the outside — MCP, CLI, hooks, the web console —
is a client of this app (Memory-contracts-v3 §9). It owns the registry, the
journal and (from phase 3) the queue and the watcher; nothing else writes an
index.

Shape of the thing:

* bound to ``127.0.0.1``. ``/mcp``, ``/mcp-admin`` and ``/mcp-tools`` are
  guarded by a bearer token (``STATE_DIR/api.token`` for the service token,
  minted lazily on first use of one of those three faces). ``/api`` — the
  console and CLI's own channel — is **open by default**: a loopback-only
  local UI gated behind a login token was friction with no real security
  benefit against local access, so `/api` requires the token only when
  deliberately gated (``$MNEMO_API_TOKEN``, or the explicit
  ``require_login`` machine setting — see ``_api_gated()``), never merely
  because ``api.token`` happens to exist on disk (MN-19: that file can be
  minted by an unrelated ``/mcp-admin``/``/mcp-tools`` call). ``/health`` and
  the ``/ui`` assets never needed one (``service_ctl`` must be able to ask
  "are you alive" before it knows where the token lives);
* one error envelope for every failure (§9.2), so a client never has to guess
  whether a 4xx body is a string, a list or a FastAPI ``detail``;
* search answers with an explicit **state** — ``indexing`` / ``empty`` /
  ``ready`` — because "nothing indexed yet" and "no match" are different
  facts and an agent must act differently on them.

Phase 2 builds the registry, the journal and the endpoints. The queue and the
watcher arrive in phase 3: the WebSocket channel is wired here, but nothing
produces progress events yet, and the seams onto ``workqueue`` are guarded
imports rather than stand-ins — a missing component reports itself instead of
returning plausible-looking nothing.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import io
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager, contextmanager, redirect_stdout, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Literal


from fastapi import (
    Body, FastAPI, Query, Request, Security, WebSocket, WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import (
    agent_registry, agent_runtime, catalog, config, engine_update, presets,
    registry, servicelog, settings, store,
)
from .config import TOP_K
from .providers import EmbeddingUnavailable, forget_providers, get_provider
from .registry import AmbiguousBankRef, Bank, BankExists, BankNotFound

log = logging.getLogger("mnemo.api")

# --------------------------------------------------------------- settings
# Resolved from config once its `api / websocket (J)` section lands; until
# then from the environment, with the contract's defaults (§12).


def _cfg(name: str, env: str, default: Any, cast: Any = str) -> Any:
    configured = getattr(config, name, None)
    if configured is not None:
        return configured
    raw = os.environ.get(env)
    if raw is None:
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def _detect_service_version() -> str:
    """The tag this running process actually is, not a source literal.

    ``config.SERVICE_VERSION`` is a hardcoded string nobody bumps per
    release (confirmed: it read "3.0.0" while genuinely running v3.0.1 on a
    real machine) -- completely disconnected from the self-update tag-
    tracking system, which is why the sidebar footer and `/api/update/status`
    could disagree about what version this even is. Deriving both from the
    same `engine_update.effective_current_tag()` ties them to one source of
    truth; the literal remains only as the last-resort fallback for a
    devserver/test run outside the versioned `~/.mnemo/versions/<tag>/`
    layout, where there is nothing to detect.
    """
    from . import engine_update  # noqa: PLC0415 - avoid a top-level sibling import

    return (
        engine_update.effective_current_tag(engine_update.read_state())
        or getattr(config, "SERVICE_VERSION", "3.0.0")
    )


SERVICE_VERSION: str = _detect_service_version()
API_HOST: str = _cfg("API_HOST", "MNEMO_API_HOST", "127.0.0.1")
API_PORT: int = int(_cfg("API_PORT", "MNEMO_API_PORT", 4646, int))


def token_file() -> Path:
    """Resolved per call, through ``config`` — never bound at import.

    ``from .config import STATE_DIR`` captures the Path object once, so a test
    or container that repoints ``config.STATE_DIR`` afterwards would keep
    reading and writing the original directory. Every state path below is a
    function for that reason.
    """
    return Path(config.STATE_DIR) / "api.token"


def service_info_file() -> Path:
    """Derived live, deliberately NOT read from ``config.SERVICE_INFO_FILE``.

    That constant is ``STATE_DIR / "service.json"`` evaluated at import, so it
    carries the same frozen-path bug this function exists to avoid: relocate
    ``config.STATE_DIR`` and the backend would announce itself in the old
    directory while everything else moved. Same value, computed when asked.
    (``config.SERVICE_PID_FILE`` has the identical shape — reported.)
    """
    return Path(config.STATE_DIR) / "service.json"


FILE_MAX_BYTES: int = int(
    _cfg("FILE_MAX_BYTES", "MNEMO_FILE_MAX_BYTES", 2 * 1024 * 1024, int)
)
WS_PING_INTERVAL_S: float = float(getattr(config, "WS_PING_INTERVAL_S", 30.0))

_started_at = time.time()
_started_iso = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


# ------------------------------------------------------------------ token


_token: str | None = None


def api_token() -> str:
    """The bearer token, created on first use.

    ``$MNEMO_API_TOKEN`` wins over the file so a container or a test can pin
    one without touching state.
    """
    global _token
    if _token:
        return _token
    env = os.environ.get("MNEMO_API_TOKEN")
    if env and env.strip():
        _token = env.strip()
        return _token
    path = token_file()
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        _token = existing
        return _token
    _token = secrets.token_hex(24)  # 48 hex chars, same shape as embed.token
    # An unwritable state dir must not turn every request into a 500 from the
    # auth middleware: keep the in-memory token and carry on degraded.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_token, encoding="utf-8")
    except OSError as exc:
        log.error("cannot persist the API token to %s: %s", path, exc)
        return _token
    # POSIX-only in effect. On Windows this clears the read-only bit and
    # nothing more — the real protection there is the user-profile ACL, which
    # is adequate, but this line does not provide it.
    with suppress(OSError):
        os.chmod(path, 0o600)
    return _token


def _token_ok(presented: str | None) -> bool:
    if not presented:
        return False
    return secrets.compare_digest(presented.strip(), api_token())


def _configured_token() -> str | None:
    """The service token if one exists — never mints one (unlike `api_token()`).

    What `/api` checks against. `/mcp-admin` and `/mcp-tools` still want a
    token to always exist, so they call `api_token()`, which lazily creates
    one on first use. `/api` wants the opposite default — open on loopback
    until someone deliberately opts in (`$MNEMO_API_TOKEN`, or a future
    explicit "generate" step) — so it must be able to ask "does one exist"
    without that question itself conjuring one into being.
    """
    env = os.environ.get("MNEMO_API_TOKEN")
    if env and env.strip():
        return env.strip()
    try:
        existing = token_file().read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    return existing or None


def _api_gated() -> bool:
    """Whether `/api` (and `/ws`) must present a token right now (MN-19).

    Deliberately NOT "does `api.token` exist on disk" — that file may have
    been minted by an unrelated `/mcp-admin`/`/mcp-tools` call, and its mere
    presence must not silently start gating `/api` for someone who never
    opted in. Only two things may gate it: `$MNEMO_API_TOKEN` set (unchanged,
    pre-dates this toggle) or the explicit `settings.require_login()` switch.
    """
    env = os.environ.get("MNEMO_API_TOKEN")
    if env and env.strip():
        return True
    return settings.require_login()


# What a caller who reached `/mcp` with the wrong kind of credential needs to
# read. A bare "missing or invalid API token" is true and useless here: the
# most likely holder of a rejected token is someone presenting the *service*
# token, which is a perfectly valid credential on three other surfaces.
_MCP_401 = (
    "the MCP face is addressed by a bank token: the token identifies the "
    "bank, and nothing else does. This one does not match any registered "
    "bank.\n"
    "If you just cloned this project, its `.mcp.env` is not in git and your "
    "MNEMO_TOKEN is still blank. Fix it in three steps: open the console "
    "(`mnemo ui`), copy the token of the bank this project uses, paste it as "
    "MNEMO_TOKEN in `.mcp.env`, then run `bash mcp-setup.sh` to regenerate "
    "`.mcp.json`. A project with no `.mcp.json.template` instead gets its "
    "token written straight in by `mnemo init`.\n"
    "If you presented the SERVICE token: it does not open this face — it has "
    "no bank to resolve to — and belongs on /mcp-admin or /mcp-tools."
)

# Same, for a URL that still carries the old `/mcp/<bank>` segment. Checked
# before auth on purpose: the commonest way to arrive here is a config written
# against the previous shape, whose token is *valid* — telling that caller
# "unauthorized" would send them hunting for a credential problem that is not
# there.
_MCP_SEGMENT = (
    "the MCP face takes no path segment — the bank comes from the token. "
    "Use http://<host>:<port>/mcp?token=<bank-token>; if this URL came from "
    "an older `.mcp.json`, re-run `mnemo init --migrate`."
)


# ------------------------------------------------------------------ errors


_ERROR_STATUS: dict[str, int] = {
    "unauthorized": 401,
    "bad_request": 400,
    "validation_error": 422,
    "bank_not_found": 404,
    "bank_ambiguous": 409,
    "bank_exists": 409,
    "root_not_found": 400,
    "path_outside_bank": 400,
    "file_not_found": 404,
    "embed_unavailable": 503,
    # [NEW beyond §9.2] A file WE hold open, or that the worker is still
    # writing. `internal` said only "something broke"; this names the cause
    # and the fix, and it is a state the user can actually resolve.
    "index_locked": 409,
    # [NEW beyond §9.2] The vectors in the index were built by a different
    # provider than the one that would embed the query. Answering anyway is
    # not an option: at a different width sqlite-vec raises, and — worse — at
    # the SAME width it does not, and quietly ranks one vector space against
    # another. Its own 409 rather than `internal`, because this is a state
    # with a fix the caller can carry out (rebuild the bank).
    "bank_stale": 409,
    # [NEW beyond §9.2] Unload/load asked for while the worker is indexing.
    # 409 rather than 503: nothing is broken and nothing is unreachable —
    # the machine is simply in a state where this action would damage work
    # already underway, and it will not be a moment from now.
    "embed_busy": 409,
    # [NEW beyond §9.2] The unload/load itself did not take: a resident that
    # answers someone else's token, an Ollama that cannot be reached, an
    # endpoint that refused the probe.
    "embed_control_failed": 502,
    # [NEW beyond §9.2] `POST /api/embed/download` refused: the model is
    # already on disk, so a download would only repeat 2.2 GB of work the
    # button exists to avoid.
    "already_cached": 409,
    # [NEW beyond §9.2] A previously spawned `warmup --force` is still
    # running. Not `embed_busy` — the queue is uninvolved — but the same
    # shape: nothing is broken, the action just cannot run twice at once.
    "download_in_progress": 409,
    # The orphan list could not be trusted (most importantly: banks.json was
    # unreadable). 409 because the request conflicts with the machine's current
    # state; retrying after the registry is fixed is the remedy, not a server
    # restart and never a guess that every index is disposable.
    "orphan_cleanup_refused": 409,
    # [NEW beyond §9.2] `POST /api/update/apply`'s `tag` no longer matches
    # `last_check.latest_tag` — the same principle as orphan cleanup only
    # accepting ids it just showed: a target confirmed a moment ago can be
    # stale by the time the request lands (a newer release appeared, or the
    # tag was already applied by another client). 409, not 400: the request
    # is well-formed, it just no longer matches the machine's current state.
    "stale_target": 409,
    # [NEW beyond §9.2] A staging/apply cycle from an earlier `POST
    # /api/update/apply` is already running in this process. Same shape as
    # `download_in_progress`: nothing is broken, the action just cannot run
    # twice at once (two concurrent `stage_release()` calls would race each
    # other building the same `versions/<tag>/`).
    "update_in_progress": 409,
    # [NEW beyond §9.2] `POST /api/update/auto/confirm|cancel` called with
    # nothing pending -- the timer already fired, it was already cancelled,
    # or nothing was ever armed. 404-family: the "pending countdown" this
    # request targets does not exist right now, same shape as any other
    # "the thing you named is not there" response.
    "auto_not_pending": 404,
    # [NEW beyond §9.2, MN-40] No agent matches the slug — same shape as
    # `bank_not_found`.
    "agent_not_found": 404,
    # [NEW beyond §9.2, MN-40] `create()` refused: the target folder already
    # exists and is not empty. The fix is `adopt`, not a retry of `create`.
    "agent_exists": 409,
    # [NEW beyond §9.2, MN-40] `POST /api/agents` targets a non-empty folder
    # without `confirm_adopt=true`. The response carries the same preview the
    # console would show before asking — same shape as `orphan_cleanup_refused`
    # only accepting ids it just displayed.
    "adoption_confirmation_required": 409,
    # [NEW beyond §9.2, MN-40] `launch.json` — supplied in the request body,
    # or already on disk for an existing agent — fails validation. 400, not
    # 500: this is a well-formed request describing a document that does not
    # meet the schema, not a server fault.
    "invalid_launch_config": 400,
    # [NEW beyond §9.2, MN-41] No catalog entry matches the given id — same
    # shape as `bank_not_found` / `agent_not_found`.
    "catalog_entry_not_found": 404,
    # [NEW beyond §9.2, MN-41] `content` fails validation for its category:
    # empty, or not valid JSON for an `mcp` entry. 400, not 422: this is
    # domain validation on a field's own value, not a malformed request body.
    "invalid_catalog_entry": 400,
    # [NEW beyond §9.2, MN-41] Either an explicit rename collides with
    # another entry in the same category, or — for `mcp` — the content
    # canonicalises to a config another entry already carries. 409, same
    # family as `bank_exists`/`agent_exists`: the request is well-formed, it
    # just conflicts with what is already registered.
    "catalog_entry_exists": 409,
    # [NEW beyond §9.2, MN-41] `DELETE /api/catalog/{id}` refused: at least
    # one agent still references this entry. No force-delete — the response
    # lists every referencing agent so the caller can detach first, same
    # "show the blocker, let the human decide" shape as
    # `orphan_cleanup_refused`.
    "entry_in_use": 409,
    # [NEW beyond §9.2, MN-48] `PATCH/PUT /api/agents/{slug}/...` — the
    # linked-catalog-entry endpoints below. Same reasoning throughout as the
    # matching bank/catalog codes above: 404 for "does not exist", 400 for
    # "well-formed request, invalid value", 409 for "conflicts with what is
    # already there".
    "category_mismatch": 400,
    "link_exists": 409,
    "link_name_exists": 409,
    "link_not_found": 404,
    "unknown_var": 400,
    "path_conflict": 409,
    "invalid_substituted_config": 400,
    # [NEW beyond §9.2, MN-43] No chat matches the given (agent, chat_id) —
    # same shape as `link_not_found`.
    "chat_not_found": 404,
    # [NEW beyond §9.2, MN-43] The machine-wide live-session cap
    # (`config.MAX_LIVE_SESSIONS`) is already reached. Reachable in practice
    # only via the WS route's own error envelope (real spawns are lazy, on
    # first WS subscriber) — mapped here too so an HTTP surface that ever
    # needs to report it agrees with the WS one.
    "too_many_sessions": 409,
    "internal": 500,
}


class ApiError(Exception):
    """A failure with a code from the §9.2 table."""

    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or None
        self.status = _ERROR_STATUS.get(code, 500)


def _envelope(code: str, message: str, detail: Any = None) -> dict:
    body: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return {"error": body}


# ------------------------------------------------------- queue (phase 3)


_queue_mod: Any = None
_queue_checked = False


def _queue() -> Any:
    """The work queue, once phase 3 ships it.

    A guarded import rather than a stub: with no queue running, "0 queued" is
    the truth, but *enqueueing* has nowhere to go and must say so rather than
    return a task id for work nobody will do.
    """
    global _queue_mod, _queue_checked
    if not _queue_checked:
        _queue_checked = True
        try:
            from . import workqueue as _wq  # noqa: PLC0415
        except ImportError:
            _wq = None
        _queue_mod = _wq
    return _queue_mod


def _queued(bank_id: str | None = None) -> int:
    q = _queue()
    if q is None:
        return 0
    try:
        return int(q.depth(bank_id))
    except Exception:  # noqa: BLE001
        return 0


def _busy(bank_id: str | None = None) -> bool:
    q = _queue()
    if q is None:
        return False
    try:
        return bool(q.busy(bank_id))
    except Exception:  # noqa: BLE001
        return False


def _pending_paths(bank_id: str) -> list[str]:
    q = _queue()
    if q is None:
        return []
    try:
        return sorted(q.pending_paths(bank_id))
    except Exception:  # noqa: BLE001
        return []


def _require_queue() -> Any:
    q = _queue()
    if q is None:
        raise ApiError(
            "internal",
            "the work queue is not running: indexing is unavailable "
            "(src/workqueue.py lands in phase 3)",
        )
    return q


# ---------------------------------------------------------------- helpers


# The scope key the plain MCP face reads its bank out of, written by the auth
# middleware once it has resolved the presented token.
#
# A plain `scope` entry rather than a ContextVar set here. `BaseHTTPMiddleware`
# runs `call_next` in a child task; a ContextVar set before that call does
# propagate, but only because task creation happens to copy the current
# context — a fact about anyio that this code would then silently depend on.
# The scope is the *same dict object* all the way down to the mounted app, so
# there is nothing to reason about. `mcp_server`'s shim lifts it into the
# ContextVar the tool bodies read, inside the app where that belongs.
BANK_SCOPE_KEY = "mnemo_bank_id"


def _resolve_bank(ref: str, *, require_enabled: bool = True) -> Bank:
    try:
        bank = registry.resolve(ref)
    except AmbiguousBankRef as exc:
        raise ApiError("bank_ambiguous", str(exc), ref=ref) from exc
    except BankNotFound as exc:
        raise ApiError("bank_not_found", str(exc), ref=ref) from exc
    # The gate is `searchable`, not `enabled`: a **frozen** bank is reachable
    # by every read path — its index is held still, not switched off. Only a
    # disabled bank is hidden, and it must not look like an empty one (§6.1).
    if require_enabled and not bank.searchable:
        raise ApiError("bank_not_found", f"bank {bank.name!r} is disabled", ref=ref)
    return bank


@contextmanager
def _bank_conn(bank: Bank):
    """A short-lived read connection to a bank's index, or ``None``.

    Deliberately **not** cached. It used to be, to keep `_ensure_schema`'s
    DDL off every request — but `connect(..., ensure=False)` removes that,
    and `ensure_schema` is now read-first anyway, so the cache was saving
    only ~7 ms of connection setup on a ~200 ms search.

    What it cost was worse: on Windows an open SQLite handle makes the file
    undeletable, so `banks remove` failed with WinError 32 and left an
    orphaned 4 MB index behind. A per-thread cache also cannot be closed from
    the thread doing the removal. Correctness of a destructive operation
    beats 3% of a search.

    Never creates the file: listing banks must not leave an empty database
    behind for every root that was registered but never indexed.
    """
    if not bank.db_path.exists():
        yield None
        return
    conn = store.connect(bank.db_path, ensure=False)
    try:
        yield conn
    finally:
        conn.close()


def _queue_state(bank: Bank) -> tuple[int, bool]:
    """This bank's queue depth and whether work for it is in flight.

    Read **before** the chunk count, never after: between the two reads a
    bank can finish its first build, and reading chunks first would then
    report ``empty`` for a bank that is fully indexed — the one answer that
    makes an agent give up on it.
    """
    return _queued(bank.id), _busy(bank.id)


def _status_from(state: tuple[int, bool], chunk_count: int) -> tuple[str, int]:
    """``indexing`` > ``empty`` > ``ready`` (§5.2).

    ``indexing`` wins over ``empty`` on purpose: ``empty`` tells an agent the
    bank is pointless and it stops asking; ``indexing`` with ``chunk_count=0``
    tells it to come back.
    """
    queued, busy = state
    if queued > 0 or busy:
        return "indexing", queued
    if chunk_count == 0:
        return "empty", queued
    return "ready", queued


def _provider_identity(spec: str | None) -> tuple[dict, str | None]:
    """Name / model / dim / key of the provider that would index a bank.

    Returns ``(identity, error)``. Constructing the ``api`` provider raises
    ``ValueError`` when its URL, model or dim are unset — deliberately, so a
    misconfigured provider fails before a bulk index rather than half-way
    through one. In a *status* path that is a state the user is actively
    trying to get out of, so it is rendered, not raised.

    ``provider.key`` is ``name:model:dim`` and holds no credential; nothing
    derived from ``MNEMO_API_EMBED_KEY`` is ever exposed here.
    """
    try:
        provider = get_provider(spec)
    except ValueError as exc:
        return {}, str(exc)
    except Exception as exc:  # noqa: BLE001 - status must not fail on this
        return {}, f"{type(exc).__name__}: {exc}"
    return {
        "name": provider.name,
        "model": provider.model,
        "dim": provider.dim,
        "key": provider.key,
    }, None


def _stale_index_error(bank: Bank, index_key: str | None) -> ApiError | None:
    """``bank_stale`` when the index holds another provider's vectors.

    Comparing the whole ``provider_key`` (``name:model:dim``), never the width
    alone: the dangerous case is two different models that agree on width —
    e5-large and bge-m3 are both 1024 — where nothing raises and the ranking
    is simply meaningless. `store.needs_rebuild` compares the same key for the
    same reason.

    Normally unreachable, because the queue rebuilds a bank as soon as the
    provider changes. It is reachable for a **frozen** bank, which is the
    point of freezing, and briefly for any bank between the settings change
    and the rebuild.
    """
    if not index_key:
        return None
    active, _ = _provider_identity(bank.provider)
    live = active.get("key")
    if not live or live == index_key:
        return None
    hint = (
        "unfreeze the bank and rebuild it"
        if bank.state == registry.STATE_FROZEN
        else "the rebuild is queued — retry shortly"
    )
    return ApiError(
        "bank_stale",
        f"bank {bank.name!r} is indexed with {index_key}, but {live} is "
        f"active now — {hint}",
        ref=bank.name,
        index_provider_key=index_key,
        provider_key=live,
        state=bank.state,
    )


def _bank_info(bank: Bank) -> dict:
    """The one bank shape the API returns (§9.5)."""
    # Queue state FIRST: reading chunk_count first leaves a window where a
    # bank that finished indexing between the two reads reports `empty`.
    status_probe = _queue_state(bank)
    files = chunks = db_bytes = 0
    last_indexed: str | None = None
    index_provider_key: str | None = None
    try:
        with _bank_conn(bank) as conn:
            if conn is not None:
                files = store.file_count(conn)
                chunks = store.chunk_count(conn)
                db_bytes = bank.db_path.stat().st_size
                meta = store.get_meta(conn)
                last_indexed = meta.get("last_indexed_at")
                index_provider_key = meta.get("provider_key")
    except Exception as exc:  # noqa: BLE001 - a broken index must still list
        log.warning("cannot read index of bank %s: %s", bank.name, exc)
    status, queued = _status_from(status_probe, chunks)
    active, provider_error = _provider_identity(bank.provider)
    return {
        "id": bank.id,
        "name": bank.name,
        "root": bank.root.as_posix(),
        "provider": bank.provider,
        "state": bank.state,
        # Derived, and kept because a client written against the older shape
        # reads it. It is output only — the registry stores `state` alone.
        "enabled": bank.enabled,
        "exists": bank.exists,
        "git": bank.is_git,
        "files": files,
        "chunks": chunks,
        "db_bytes": db_bytes,
        "last_indexed": last_indexed,
        "status": status,
        "queued": queued,
        "indexing": status == "indexing",
        "last_error": servicelog.last_index_error(bank.id),
        # Which provider *would* index this bank now …
        "provider_active": active.get("name"),
        "provider_key": active.get("key"),
        # … and which one actually built the vectors that are in there.
        "index_provider_key": index_provider_key,
        # They differ -> the next reconcile re-embeds the whole bank. Showing
        # only the configured provider would leave a user watching 300 files
        # rebuild with no idea why.
        "rebuild_pending": bool(
            index_provider_key
            and active.get("key")
            and index_provider_key != active.get("key")
        ),
        "provider_error": provider_error,
    }


def _hit_json(hit: Any) -> dict:
    span = getattr(hit, "span", None)
    return {
        "chunk_uid": getattr(hit, "chunk_uid", None),
        "path": getattr(hit, "path", None),
        "heading": getattr(hit, "heading", None),
        "chunk_index": getattr(hit, "chunk_index", None),
        "span": list(span) if span else None,
        "score": getattr(hit, "score", None),
        "sim": getattr(hit, "sim", None),
        "content": getattr(hit, "content", None),
    }


def _parse_time(value: str | None) -> float | None:
    """ISO-8601 or epoch seconds — both accepted for ``since`` / ``until``."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ApiError("bad_request", f"cannot parse time {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


# ------------------------------------------------------- exclude matching


def _glob_re(pattern: str) -> re.Pattern[str]:
    """Translate one ``.gitignore``-ish glob to a regex over POSIX relpaths.

    Only what the registry's ``exclude`` needs: ``**/`` (any depth), ``**``,
    ``*`` and ``?`` (never crossing ``/``).
    """
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _compile_excludes(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [_glob_re(p) for p in patterns]


def _excluded(rel: str, patterns: list[re.Pattern[str]], *, is_dir: bool) -> bool:
    if any(p.match(rel) for p in patterns):
        return True
    # ".git/**" excludes the contents of .git, which in practice means the
    # directory itself is not worth descending into.
    return is_dir and any(p.match(f"{rel}/_") for p in patterns)


# -------------------------------------------------------------- websocket


class Hub:
    """Fan-out for service events (§9.7).

    Phase 3's producers run on worker threads, so ``publish`` is thread-safe
    and hands the envelope to the event loop; nothing here blocks the worker.
    """

    def __init__(self) -> None:
        self._clients: dict[WebSocket, str | None] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._clients)

    async def connect(self, ws: WebSocket, bank_id: str | None) -> None:
        await ws.accept()
        with self._lock:
            self._clients[ws] = bank_id

    def disconnect(self, ws: WebSocket) -> None:
        with self._lock:
            self._clients.pop(ws, None)

    def subscribe(self, ws: WebSocket, bank_id: str | None) -> None:
        with self._lock:
            if ws in self._clients:
                self._clients[ws] = bank_id

    def envelope(self, type_: str, data: dict, bank_id: str | None) -> dict:
        return {
            "v": 1,
            "type": type_,
            "ts": _now_iso(),
            "bank_id": bank_id,
            "data": data,
        }

    async def send(self, ws: WebSocket, envelope: dict) -> None:
        with suppress(Exception):
            await ws.send_json(envelope)

    async def broadcast(self, envelope: dict) -> None:
        bank_id = envelope.get("bank_id")
        with self._lock:
            targets = [
                ws
                for ws, filt in self._clients.items()
                if filt is None or bank_id is None or filt == bank_id
            ]
        if not targets:
            return

        async def deliver(ws: WebSocket) -> WebSocket | None:
            try:
                # A client that has stopped reading must not hold up the
                # others — or the ping loop, which is what would notice it.
                await asyncio.wait_for(ws.send_json(envelope), timeout=5.0)
                return None
            except Exception:  # noqa: BLE001 - a dropped client is not an error
                return ws

        # Concurrent, not sequential: with N clients this is one round trip,
        # and one stalled socket costs only its own timeout.
        for dead in await asyncio.gather(*(deliver(ws) for ws in targets)):
            if dead is not None:
                self.disconnect(dead)

    def publish(self, type_: str, data: dict, bank_id: str | None = None) -> None:
        """Thread-safe entry point for producers outside the event loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        envelope = self.envelope(type_, data, bank_id)
        with suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(self.broadcast(envelope), loop)


hub = Hub()


_EMPTY_QUEUE = {"depth": 0, "high": 0, "normal": 0, "low": 0,
                "current": None, "by_bank": {}}


def _queue_snapshot_json() -> dict:
    q = _queue()
    if q is None:
        return dict(_EMPTY_QUEUE)
    try:
        snap = q.snapshot()
    except Exception:  # noqa: BLE001
        return dict(_EMPTY_QUEUE)
    current = getattr(snap, "current", None)
    return {
        "depth": snap.depth,
        "high": snap.high,
        "normal": snap.normal,
        "low": snap.low,
        # Service-wide totals cannot tell the console which of 12 queued
        # tasks belong to which bank — and a bank list is exactly that view.
        # The worker already has the breakdown, so it travels with every
        # queue event and is the single source for per-bank counters.
        "by_bank": getattr(snap, "by_bank", {}) or {},
        "current": None
        if current is None
        else {
            "task_id": current.id,
            "bank_id": current.bank_id,
            "kind": current.kind,
            "path": current.path,
            "batch": getattr(snap, "current_batch", 0),
            "batches": getattr(snap, "current_batches", 0),
            # Absolute epoch seconds, so a page reloaded mid-index shows the
            # task's real elapsed time instead of counting from the reload.
            # Same field and unit as the `queue` WS delta — if only one
            # carried it the counter would reset on the next event.
            "started_at": getattr(snap, "current_started_at", 0.0),
        },
    }


# -------------------------------------------------------------- lifecycle


# Whether a bank is currently in the "failed" state, so `bank_status` fires
# on the transition rather than on every task.
_bank_failed: dict[str, bool] = {}


def _on_queue_event(ev: dict) -> None:
    """The queue's single outlet, fanned out here.

    ``workqueue`` knows about neither the socket nor the journal — it hands
    over §9.7 envelopes and this decides what they mean. Terminal events are
    the ones worth a journal row: a per-batch progress tick is live state,
    not history, and writing 300 of them per file would bury the events that
    answer "what did the service actually do".

    ``bank_status`` is deliberately NOT sent per finished task. Counters live
    in ``queue.by_bank``, which the worker already computes and which arrives
    on every queue change; re-deriving a whole BankInfo per file would reopen
    the index hundreds of times during a bulk to say what the counters
    already said. This fires only on a *slow* change — here, a bank entering
    or leaving the failed state.
    """
    type_ = ev.get("type", "queue")
    data = ev.get("data", {}) or {}
    bank_id = ev.get("bank_id")
    hub.publish(type_, data, bank_id)

    if type_ not in ("index_done", "index_error") or not bank_id:
        return
    servicelog.log_index(
        bank_id=bank_id,
        kind=data.get("kind", "file"),
        trigger=data.get("trigger", "api"),
        path=data.get("path"),
        result="error" if type_ == "index_error" else data.get("result", "ok"),
        files_indexed=int(data.get("files_indexed", 0)),
        chunks_indexed=int(data.get("chunks_indexed", 0)),
        files_pruned=int(data.get("files_pruned", 0)),
        took_ms=data.get("took_ms"),
        error=data.get("error"),
    )

    failed = type_ == "index_error"
    if _bank_failed.get(bank_id, False) == failed:
        return           # no transition — the badge already says the right thing
    _bank_failed[bank_id] = failed
    with suppress(Exception):
        hub.publish("bank_status", {"bank": _bank_info(registry.get(bank_id))},
                    bank_id)


def _write_service_info() -> None:
    """``service.json`` — how ``service_ctl`` finds a running backend (§11.2)."""
    import sys

    info = {
        "pid": os.getpid(),
        "port": API_PORT,
        "host": API_HOST,
        "started_at": _started_iso,
        "version": SERVICE_VERSION,
        "python": Path(sys.executable).as_posix(),
    }
    path = service_info_file()
    with suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")


def _reconcile_on_start() -> None:
    """Catch up on whatever changed while the service was down (§9.6 step 4)."""
    if str(os.environ.get("MNEMO_RECONCILE_ON_START", "1")).strip() == "0":
        return
    q = _queue()
    if q is None:
        log.info("reconcile-on-start skipped: no work queue yet (phase 3)")
        return
    for bank in registry.load():
        # `watched`: a frozen bank is deliberately left as it is, including
        # across a restart. Catching it up here would undo the freeze on the
        # next service start, which is the one moment the user is not looking.
        if not bank.watched:
            continue
        rebuild = False
        if bank.db_path.exists():
            conn = None
            try:
                conn = store.connect(bank.db_path)
                provider = get_provider(bank.provider)
                rebuild = store.needs_rebuild(
                    conn, provider_key=provider.key, dim=provider.dim
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("cannot inspect bank %s: %s", bank.name, exc)
            finally:
                if conn is not None:
                    conn.close()
        with suppress(Exception):
            q.enqueue_bulk(bank.id, trigger="startup", rebuild=rebuild)


async def _ping_loop() -> None:
    while True:
        await asyncio.sleep(WS_PING_INTERVAL_S)
        await hub.broadcast(hub.envelope("ping", {}, None))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start order is §9.6 — journal, registry, catch-up, queue, watcher, then
    # announce ourselves. The service token is deliberately NOT minted here
    # any more: `api_token()` is now lazy, called only when `/mcp-admin` or
    # `/mcp-tools` are first actually used. Eagerly creating it here would
    # mean a token file always exists by the time anything checks — which is
    # exactly the false signal `/api`'s gate (`_api_gated()`) was built to
    # ignore (MN-19), but `_configured_token()`'s doctor-only "present"
    # reporting would still be misleading if a token always existed.
    servicelog.connect()
    servicelog.start_pruner()
    with suppress(Exception):
        engine_update.start_checker()
    registry.load(force=True)
    # Banks registered before per-bank tokens existed get one here, in place:
    # a migration that adds a field, not a rewrite of the document.
    with suppress(Exception):
        registry.ensure_tokens()
    _reconcile_on_start()

    q = _queue()
    if q is not None:
        with suppress(Exception):
            q.start(on_event=_on_queue_event)
    try:
        from . import watcher as _watcher  # noqa: PLC0415
    except ImportError:
        _watcher = None
    if _watcher is not None:
        with suppress(Exception):
            _watcher.start()

    hub.bind(asyncio.get_running_loop())
    agent_runtime.bind_loop(asyncio.get_running_loop())
    ping = asyncio.create_task(_ping_loop())
    _write_service_info()
    log.info("mnemo backend %s on %s:%s", SERVICE_VERSION, API_HOST, API_PORT)

    # Both MCP apps are mounted, so FastAPI does not run their lifespans for
    # us — each session manager has to be entered by hand or every request to
    # that face fails on a missing task group. Two instances, two managers:
    # forgetting the second is a 500 on the admin face only, which is exactly
    # the kind of failure that hides.
    from .mcp_admin import server as mcp_admin  # noqa: PLC0415
    from .mcp_server import server as mcp_server  # noqa: PLC0415

    async with mcp_server().session_manager.run():
        async with mcp_admin().session_manager.run():
            try:
                yield
            finally:
                await _shutdown(ping, q, _watcher)
    return


async def _shutdown(ping, q, watcher_mod) -> None:
    ping.cancel()
    with suppress(asyncio.CancelledError):
        await ping
    hub.bind(None)
    # Waits for real process termination (join with a timeout), same
    # contract as `q.stop()` below — a live `claude` process must not be
    # left running, unmonitored, once the backend that owns it is gone.
    with suppress(Exception):
        agent_runtime.stop_all()
    agent_runtime.bind_loop(None)
    if watcher_mod is not None:
        with suppress(Exception):
            watcher_mod.stop()
    if q is not None:
        with suppress(Exception):
            q.stop()
    with suppress(Exception):
        engine_update.stop_checker()
    servicelog.stop_pruner()
    servicelog.close()
    with suppress(OSError):
        service_info_file().unlink()


app = FastAPI(
    title="mnemo",
    version=SERVICE_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)


# ------------------------------------------------------- auth + envelope


_GUARDED = ("/api", "/mcp", "/mcp-tools", "/mcp-admin")
# The faces that may take the token from the query string (§9.1). `/mcp-admin`
# is here for the same reason as `/mcp`: an MCP client configures a URL, not
# headers, and the admin face is reached the same way.
_URL_TOKEN_OK = ("/mcp", "/mcp-tools", "/mcp-admin")


# Declared so the OpenAPI schema carries a security scheme and `/docs` grows an
# **Authorize** button. Without it the auth lives only in the middleware below,
# never reaches the schema, and every "Try it out" answers 401 with no way for
# the person clicking it to fix that. `auto_error=False`: the middleware is
# still the one that rejects, this only describes what it wants.
bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Contents of ~/.mnemo/state/api.token",
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # `/mcp` -> `/mcp/`, `/mcp-admin` -> `/mcp-admin/`, before anything else
    # looks at the path.
    #
    # A Starlette `Mount("/mcp")` compiles to `^/mcp/(?P<path>.*)$`, so the
    # bare path — which is now exactly what an MCP client is configured with,
    # for both faces — matches nothing and falls through to `redirect_slashes`:
    # a 307 on every single request. This used not to bite the plain face,
    # because a bank segment always followed; removing the segment is what
    # makes it the common case. Rewriting here keeps each face at one round
    # trip and removes the assumption that every MCP client follows a 307 on
    # a POST.
    #
    # Read from the scope, not from `request.url`: Starlette builds that URL
    # once and caches it, so a rewrite made after touching it would be
    # invisible to everything downstream that asks the Request.
    path = request.scope.get("path", "") or request.url.path
    for mount in ("/mcp", "/mcp-admin"):
        if path == mount:
            path = mount + "/"
            request.scope["path"] = path
            request.scope["raw_path"] = path.encode()
            break

    # A leftover `/mcp/<bank>` is rejected, not quietly accepted and ignored.
    # Swallowing it would leave a path component that does not mean what it
    # says — worse than one that is absent, because the next person reads it
    # as routing. Checked BEFORE auth deliberately: the commonest arrival here
    # is a config written against the previous shape, carrying a *valid* bank
    # token, and answering that caller "unauthorized" would send them hunting
    # for a credential problem that does not exist.
    if path.startswith("/mcp/") and path != "/mcp/":
        return JSONResponse(_envelope("bad_request", _MCP_SEGMENT),
                            status_code=400)

    if path.startswith(_GUARDED):
        header = request.headers.get("authorization", "")
        presented = (
            (header[7:] if header.lower().startswith("bearer ") else None)
            or request.headers.get("x-mnemo-token")
            # `/mcp/<bank>?token=…` — the MCP face carries its token in the
            # URL precisely so it does not depend on a client forwarding
            # headers, and `/mcp-tools/*` accepts it because that surface
            # exists to be poked with curl. Nowhere else: an /api caller has
            # no reason to put a secret where it lands in shell history and
            # proxy logs. Spelled out rather than left to `/mcp-tools`
            # happening to start with `/mcp` — renaming one must not silently
            # change the other's auth.
            or (request.query_params.get("token")
                if path.startswith(_URL_TOKEN_OK) else None)
        )
        # The auth matrix (§9.1). Each surface takes exactly ONE kind of
        # credential — there is no longer a face that accepts two:
        #
        #   /mcp          a BANK token only. The token *is* the address, so a
        #                 service token here has no bank to resolve to and
        #                 accepting it would mean guessing which one.
        #   /mcp-admin    the service token ONLY — never a bank token, or a
        #                 project's own wiring could add and drop banks.
        #   /mcp-tools/*  the service token (it keeps the explicit `bank`
        #                 parameter, so no single bank's token is the key).
        #   /api/*        the service token — but ONLY when `_api_gated()`
        #                 says so: `$MNEMO_API_TOKEN` set, or the explicit
        #                 `require_login` toggle (MN-19). NOT "does a token
        #                 file happen to exist" — `/mcp-admin`/`/mcp-tools`
        #                 mint one lazily on their own first use, and that
        #                 must not silently start gating `/api` for someone
        #                 who never opted in. With neither set (the default),
        #                 `/api` is open: it is the console's and CLI's own
        #                 local channel, loopback-only, and a login token
        #                 bought no real security there while costing every
        #                 fresh `mnemo ui` open a "paste the token" screen.
        #                 (2026-08-21 decision — Memory-design-v3.md §13 —
        #                 refined 2026-08-25 by MN-19.)
        #
        # `/mcp/` is matched with its trailing slash, which the normalisation
        # above guarantees. `/mcp-admin/` and `/mcp-tools/…` start with
        # `/mcp-`, so they cannot fall into this branch by accident.
        if path.startswith("/mcp/"):
            bank = registry.resolve_by_token(presented)
            if bank is None:
                # A plain 401 BEFORE the MCP handshake. Letting an
                # unauthorised request reach the protocol layer makes Claude
                # Code surface a transport error the user cannot act on,
                # instead of "not authorised".
                return JSONResponse(_envelope("unauthorized", _MCP_401),
                                    status_code=401)
            request.scope[BANK_SCOPE_KEY] = bank.id
        elif path.startswith("/api"):
            if _api_gated() and not (
                presented and secrets.compare_digest(presented.strip(), api_token())
            ):
                return JSONResponse(
                    _envelope("unauthorized", "missing or invalid API token"),
                    status_code=401,
                )
        elif not _token_ok(presented):
            return JSONResponse(
                _envelope("unauthorized", "missing or invalid API token"),
                status_code=401,
            )
    return await call_next(request)


@app.exception_handler(ApiError)
async def _api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        _envelope(exc.code, exc.message, exc.detail), status_code=exc.status
    )


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        _envelope("validation_error", "request body failed validation",
                  json.loads(json.dumps(exc.errors(), default=str))),
        status_code=422,
    )


@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    # A route that does not exist gets an EMPTY body — no envelope.
    #
    # The envelope is this API's way of describing a *domain* failure, and a
    # path nobody registered is not one: there is no bank, no query and no
    # rule that was broken, only an address that means nothing here.
    #
    # It also actively lies to one caller. An MCP client that gets 401 starts
    # OAuth discovery and probes `/.well-known/oauth-*`; RFC 6749 says an
    # OAuth error body is `{"error": "<string>"}`, while ours makes `error` an
    # object. The client's schema check fails on that field and it reports
    # "404 Not Found" — burying the real 401, which carries a precise
    # explanation of the stale token that actually caused this. Three separate
    # sessions chased that phantom 404 (`topics/search-quality.md` A6).
    #
    # 404 with no body says exactly as much and cannot be misparsed. Every
    # other framework failure — 405, 401, anything else — keeps the envelope:
    # those name something the caller did, and a client acting on them is
    # asking our API a question, not a spec's.
    if exc.status_code == 404:
        return Response(status_code=404)
    code = {401: "unauthorized", 405: "bad_request"}.get(
        exc.status_code, "internal"
    )
    return JSONResponse(
        _envelope(code, str(exc.detail)), status_code=exc.status_code
    )


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(_envelope("internal", str(exc)), status_code=500)


# --------------------------------------------------------------- schemas


class SearchRequest(BaseModel):
    bank: str
    query: str
    top_k: int = Field(default=TOP_K, ge=1, le=50)
    path_prefix: str | None = None
    expand_window: int | None = Field(default=None, ge=0, le=10)
    face: str = "http"


class AddBankRequest(BaseModel):
    root: str
    name: str | None = None
    provider: str | None = None
    # "connect the project (MCP)" checkbox in the add-bank dialog — runs
    # `mnemo init` against the project the bank root implies, right after
    # registration.
    init: bool = False
    # "create structure here (.claude/memory)" checkbox in the add-bank
    # dialog — seeds the bare memory tree at `root` before registration, so
    # a bare folder with no `.claude` yet can become a bank in one step.
    create_structure: bool = False


class ReindexRequest(BaseModel):
    bank: str
    path: str | None = None
    full: bool = False


class CleanOrphansRequest(BaseModel):
    # The ids the console just displayed. The endpoint re-lists and re-checks
    # each one; accepting "all" would let an index that appeared after the
    # confirmation be deleted without ever having been shown.
    ids: list[str] = Field(default_factory=list, max_length=1000)


class UpdateApplyRequest(BaseModel):
    # Must equal the CURRENT `last_check.latest_tag` — see api_update_apply's
    # "stale_target" check. Not optional/defaulted: an apply with no tag
    # named would be applying whatever the server happens to think is
    # latest at the moment the request is finally handled, not what the
    # caller saw when they decided to click.
    tag: str


class PatchBankRequest(BaseModel):
    """Editable fields of a registered bank. Omitted means unchanged.

    ``root`` is absent on purpose: the bank id is derived from it, so moving
    a root is a remove plus an add, never an edit (`registry.update`).
    """

    state: str | None = None
    name: str | None = None
    provider: str | None = None


# -------------------------------------------------------------- endpoints


@app.get("/health")
def health() -> dict:
    """Liveness — deliberately token-free (§9.1)."""
    identity, provider_error = _provider_identity(None)
    try:
        embed = {
            "provider": identity.get("name"),
            "model": identity.get("model"),
            "reachable": bool(get_provider().health()) if identity else False,
            "host": getattr(config, "EMBED_HOST", None),
            "port": getattr(config, "EMBED_PORT", None),
        }
    except Exception as exc:  # noqa: BLE001 - health never fails
        embed = {"provider": None, "reachable": False, "error": str(exc)}
    if provider_error:
        embed["error"] = provider_error
    try:
        banks = len(registry.load())
    except Exception:  # noqa: BLE001
        banks = 0
    return {
        "ok": True,
        "version": SERVICE_VERSION,
        "pid": os.getpid(),
        "port": API_PORT,
        "uptime_s": round(time.time() - _started_at, 1),
        "banks": banks,
        "queue_depth": _queued(),
        "embed": embed,
    }


def _engine_search(conn, req: SearchRequest, bank: Bank) -> list[Any]:
    """Call ``search.search`` on the v3 contract (§5).

    Imported here, not at module import: ``search`` pulls the embedder, and
    the backend must not drag ONNX into its own process just to start.
    The signature check is the seam onto engine-dev — if ``search.py`` is
    still the v2 shape we say so, loudly, instead of inventing results.
    """
    from . import search as search_mod  # noqa: PLC0415

    params = inspect.signature(search_mod.search).parameters
    if "conn" not in params:
        raise ApiError(
            "internal",
            "src/search.py still has the v2 signature (no `conn`); "
            "the v3 search contract (§5) is not in place yet",
        )
    try:
        return search_mod.search(
            conn,
            req.query,
            provider=get_provider(bank.provider),
            top_k=req.top_k,
            path_prefix=req.path_prefix,
            expand_window=req.expand_window,
        )
    except search_mod.DimensionMismatch as exc:
        # The width check above should have caught this from the provider key
        # alone. It is repeated here because the key is only as good as what
        # the last writer recorded, and this one is measured against the
        # actual column — a `500 internal` for a knowable state is the worst
        # of the available answers.
        raise ApiError(
            "bank_stale",
            f"bank {bank.name!r} holds vectors of a different width — "
            f"rebuild it ({exc})",
            ref=bank.name,
            state=bank.state,
        ) from exc


@app.post("/api/search", include_in_schema=False)
def api_search(req: SearchRequest) -> dict:
    bank = _resolve_bank(req.bank)
    started = time.perf_counter()
    degraded: str | None = None
    hits: list[Any] = []
    chunks = 0
    # Queue state before chunk count, so a bank that finishes mid-request
    # cannot be reported as `empty` (see _status_from).
    status_probe = _queue_state(bank)
    with _bank_conn(bank) as conn:
        if conn is not None:
            chunks = store.chunk_count(conn)
        status, queued = _status_from(status_probe, chunks)
        # No chunks means no possible match — never load a model to prove it.
        if chunks:
            # Refuse before embedding, not after: this is a property of the
            # index, and asking the provider first would spend a model load
            # (or a paid API call) to reach the same answer.
            stale = _stale_index_error(bank, store.get_meta(conn).get("provider_key"))
            if stale is not None:
                raise stale
            try:
                hits = _engine_search(conn, req, bank)
            except EmbeddingUnavailable as exc:
                # NFR-10: degrade, do not fail. The caller still learns the
                # bank's real state and that the answer is incomplete.
                degraded = "embed_unavailable"
                log.warning("search degraded on bank %s: %s", bank.name, exc)
    took_ms = (time.perf_counter() - started) * 1000

    servicelog.log_query(
        bank_id=bank.id,
        face=req.face,
        query=req.query,
        path_prefix=req.path_prefix,
        status=status,
        hits=hits,
        took_ms=took_ms,
    )
    hub.publish(
        "query",
        {
            "face": req.face,
            "query": req.query,
            "status": status,
            "n_hits": len(hits),
            "took_ms": round(took_ms, 1),
        },
        bank.id,
    )
    body = {
        "bank_id": bank.id,
        "bank_name": bank.name,
        "query": req.query,
        "status": status,
        "queued": queued,
        "chunk_count": chunks,
        "took_ms": round(took_ms, 1),
        "hits": [_hit_json(h) for h in hits],
    }
    if degraded:
        body["degraded"] = degraded
    return body


@app.get("/api/banks", include_in_schema=False)
def api_banks() -> dict:
    return {"banks": [_bank_info(b) for b in registry.load()]}


def _project_root_from_bank(bank_root: Path) -> Path | None:
    """`<project>/.claude/memory` -> `<project>`, else None (ineligible).

    `scaffold.init_project` always computes the bank folder itself as
    `<root>/.claude/memory` and never accepts an arbitrary one, so this is
    the only shape `init` can be pointed at.
    """
    parts = bank_root.parts
    if len(parts) >= 2 and parts[-1].lower() == "memory" and parts[-2].lower() == ".claude":
        return bank_root.parents[1]
    return None


def _run_init_for_bank(bank: Bank) -> dict:
    """Run `mnemo init` for the project this bank's root implies.

    Uses the bank's *registered* root, never the client-supplied one —
    defense in depth, the same principle already used for bank tokens
    elsewhere here. `init_project` is print-based; its stdout is captured
    rather than refactoring it into a callback, which is out of scope.
    """
    project_root = _project_root_from_bank(bank.root)
    if project_root is None:
        return {
            "ok": False,
            "skipped": True,
            "reason": "bank root does not end in .claude/memory — no "
                      "project root to wire",
        }
    from . import scaffold

    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = scaffold.init_project(
            root=str(project_root), yes=False, migrate=False
        )
    log_lines = [line for line in buf.getvalue().splitlines() if line.strip()]
    return {"ok": exit_code == 0, "log": log_lines}


@app.post("/api/banks", status_code=201, include_in_schema=False)
def api_add_bank(req: AddBankRequest) -> dict:
    if req.create_structure:
        # Resolve before trusting anything about the shape, same as
        # `api_fs_dirs`/`registry.add` do with a client-supplied path — an
        # unresolved relative `root` would otherwise get seeded against this
        # process's own cwd rather than wherever the caller meant.
        raw = Path(req.root)
        if not raw.is_absolute():
            raise ApiError("bad_request", "потрібен абсолютний шлях",
                          root=req.root)
        root = raw.expanduser().resolve()
        if root.name.lower() != "memory" or root.parent.name.lower() != ".claude":
            raise ApiError(
                "bad_request",
                "create_structure expects a <project>/.claude/memory path",
                root=req.root,
            )
        from . import scaffold
        scaffold.ensure_memory_structure(root.parent)
    try:
        bank = registry.add(req.root, name=req.name, provider=req.provider)
    except NotADirectoryError as exc:
        raise ApiError("root_not_found", str(exc), root=req.root) from exc
    except BankExists as exc:
        raise ApiError("bank_exists", str(exc), root=req.root) from exc
    info = _bank_info(bank)
    hub.publish("bank_added", {"bank": info}, bank.id)
    q = _queue()
    if q is not None:
        with suppress(Exception):
            q.enqueue_bulk(bank.id, trigger="api")
    else:
        log.info("bank %s registered; indexing waits for the queue", bank.name)
    if req.init:
        # The bank is already registered and must not be undone by an init
        # that fails or is skipped — hence a key on the response, not a
        # raised error.
        info["init"] = _run_init_for_bank(bank)
    return info


@app.get("/api/banks/{bank_id}", include_in_schema=False)
def api_bank(bank_id: str) -> dict:
    return _bank_info(_resolve_bank(bank_id, require_enabled=False))


@app.patch("/api/banks/{bank_id}", include_in_schema=False)
def api_patch_bank(bank_id: str, req: PatchBankRequest) -> dict:
    """Change a bank's state (or its name / provider).

    Leaving `enabled` behind for `state` is what makes three states possible
    at all; the one thing this endpoint must get right is what happens on the
    way *back* to `enabled`. A bank that was frozen or off has been ignoring
    its files for however long it was in that state, so switching it on and
    leaving the index as it was would present stale content as current. The
    catch-up is queued here, once, rather than waited for: the watcher only
    sees changes from now on, and `reconcile-on-start` would not run until
    the next restart.
    """
    bank = _resolve_bank(bank_id, require_enabled=False)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise ApiError("bad_request", "nothing to change", ref=bank_id)

    was_dormant = not bank.watched
    try:
        updated = registry.update(bank.id, **fields)
    except BankExists as exc:
        raise ApiError("bank_exists", str(exc), ref=bank_id) from exc
    except ValueError as exc:
        raise ApiError("bad_request", str(exc), ref=bank_id) from exc

    if was_dormant and updated.watched:
        q = _queue()
        if q is not None:
            with suppress(Exception):
                q.enqueue_bulk(updated.id, trigger="api")

    info = _bank_info(updated)
    hub.publish("bank_status", {"bank": info}, updated.id)
    log.info("bank %s is now %s", updated.name, updated.state)
    return info


# --------------------------------------------------------- per-bank tokens
#
# The credential a project's own wiring carries. `/api`, so: service token,
# hidden from the schema. Deliberately NOT part of `_bank_info` — a bank list
# is rendered in the console and pasted into issues, and a secret that rides
# along with every listing is a secret that leaks by accident. It is fetched
# for one bank, on purpose, by the one view that shows it.


def _token_json(bank: Bank, token: str) -> dict:
    return {"bank_id": bank.id, "name": bank.name, "token": token}


@app.get("/api/banks/{bank_id}/token", include_in_schema=False)
def api_bank_token(bank_id: str) -> dict:
    bank = _resolve_bank(bank_id, require_enabled=False)
    return _token_json(bank, registry.token_for(bank.id))


@app.post("/api/banks/{bank_id}/token", include_in_schema=False)
def api_regenerate_bank_token(bank_id: str) -> dict:
    """Rotate. Every `.mcp.json` pointed at this bank must be re-issued."""
    bank = _resolve_bank(bank_id, require_enabled=False)
    token = registry.regenerate_token(bank.id)
    log.info("regenerated the token for bank %s", bank.name)
    return _token_json(bank, token)


# ----------------------------------------------------------- MCP wiring (MN-13)
#
# Scoped to exactly this bank's own project — `_project_root_from_bank`,
# never a machine-wide scan (`scaffold.adopted_projects()` is the broader,
# unrelated mechanism `doctor`/`init --migrate` use). The remove-bank dialog
# needs one answer: is *this* bank's project wired, so it can offer to strip
# it in the same action.


def _mcp_wiring_info(bank: Bank) -> dict:
    project_root = _project_root_from_bank(bank.root)
    if project_root is None:
        return {"has_wiring": False, "uses_template": False, "project_root": None}
    from . import scaffold
    status = scaffold.project_mcp_wiring(project_root)
    return {
        "has_wiring": status["has_wiring"],
        "uses_template": status["uses_template"],
        "project_root": str(project_root),
    }


@app.get("/api/banks/{bank_id}/mcp-wiring", include_in_schema=False)
def api_bank_mcp_wiring(bank_id: str) -> dict:
    bank = _resolve_bank(bank_id, require_enabled=False)
    return _mcp_wiring_info(bank)


@app.delete("/api/banks/{bank_id}", include_in_schema=False)
def api_remove_bank(bank_id: str, drop_index: bool = True,
                    strip_mcp: bool = False) -> dict:
    bank = _resolve_bank(bank_id, require_enabled=False)

    # `strip_mcp`'s project root is resolved up front — a caller error (root
    # not project-shaped) is cheap to catch before touching anything, and
    # failing late here would be a confusing way to reject a bad request.
    # The actual strip, though, runs LAST, after the bank is provably gone
    # (see the comment below) — reusing "cannot half-succeed" reasoning for
    # the strip's own ordering would be backwards: unlike an unlink, a
    # successful strip cannot be undone, so running it BEFORE a step that can
    # still fail (`index_locked`) risked leaving a project's wiring stripped
    # for a bank that in fact still exists. Deferring it to last means the
    # only way to observe a half-done state is "bank is gone, wiring wasn't
    # stripped yet" — reported plainly via `mcp_strip_failed`, and recoverable
    # by re-running `mnemo init` — never "bank is still here but its wiring
    # already isn't."
    project_root: Path | None = None
    if strip_mcp:
        project_root = _project_root_from_bank(bank.root)
        if project_root is None:
            raise ApiError(
                "bad_request",
                f"bank {bank.name!r} root does not end in .claude/memory — "
                f"no project wiring to strip",
                bank_id=bank.id,
            )

    # Order matters, and this is the order that cannot half-succeed.
    #
    # Deleting the index first and the registry entry second means a failed
    # unlink leaves the bank registered — recoverable, and visible. The
    # reverse (what this used to do) removed the bank from banks.json and
    # THEN failed to delete the file, leaving a 4 MB orphan with nothing
    # pointing at it and an `internal` error on screen.
    q = _queue()
    if drop_index:
        # Nothing of ours may hold the file. Readers are already
        # request-scoped; the worker is not, so quiet the bank first.
        if q is not None and not q.drop_bank(bank.id):
            # Nothing was changed, so let the bank work again — otherwise a
            # refused removal would leave it permanently frozen.
            q.resume_bank(bank.id)
            raise ApiError(
                "index_locked",
                f"bank {bank.name!r} is still being indexed; "
                f"try again in a moment",
                bank_id=bank.id,
            )
        removed, failed = _unlink_index(bank)
        if failed:
            if q is not None:
                q.resume_bank(bank.id)
            raise ApiError(
                "index_locked",
                f"cannot delete {failed[0].name}: another process is holding "
                f"it open. The bank is still registered — nothing was lost. "
                f"Stop the service (`mnemo service stop`) and retry, or use "
                f"`--keep-index` to unregister and leave the file.",
                bank_id=bank.id,
                files=[p.name for p in failed],
            )
        log.info("removed index for bank %s (%d file(s))", bank.name, removed)

    registry.remove(bank.id, drop_index=False)
    _bank_failed.pop(bank.id, None)
    # The cancellation has to be lifted on the way out, and the reason is that
    # a bank id is DERIVED (sha1 of the root), not minted: register the same
    # folder again and it comes back with the same id. `drop_bank` was only
    # ever lifted on the failure paths above, so a successful removal left
    # that id in `_cancelled` for the life of the process -- and `enqueue`
    # answers a cancelled bank by returning a task id and dropping the task.
    # A re-added folder therefore reported "queued 1 task(s)", indexed
    # nothing, and sat at `empty` with an empty queue and an empty log.
    if q is not None:
        q.resume_bank(bank.id)
    hub.publish("bank_removed", {"bank_id": bank.id}, bank.id)

    result = {"ok": True, "index_removed": bool(drop_index)}
    if strip_mcp:
        # The bank is unregistered and its index is gone by now — this can
        # still raise, but nothing about the bank's own removal is undone by
        # that. `mcp_strip_failed` reports it plainly rather than silently
        # leaving a dead token behind.
        from . import scaffold
        assert project_root is not None
        try:
            stripped = scaffold.strip_mcp_wiring(project_root)
        except OSError as exc:
            raise ApiError(
                "mcp_strip_failed",
                f"bank {bank.name!r} was removed, but could not update MCP "
                f"wiring under {project_root}: {exc}",
                bank_id=bank.id,
            ) from exc
        result["mcp_stripped"] = stripped["touched"]
        log.info("stripped MCP wiring for bank %s under %s (%s)",
                 bank.name, project_root, ", ".join(stripped["touched"]) or "nothing found")
    return result


def _unlink_index(bank: Bank) -> tuple[int, list[Path]]:
    """Delete a bank's index and its WAL/SHM siblings.

    Returns ``(deleted, still_locked)``. The siblings matter: leaving a
    ``-wal`` next to a deleted database is the kind of debris that makes a
    later rebuild behave oddly for no visible reason.
    """
    removed, failed = 0, []
    base = bank.db_path
    for candidate in (base, base.with_name(base.name + "-wal"),
                      base.with_name(base.name + "-shm")):
        try:
            candidate.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            failed.append(candidate)
    return removed, failed


# --------------------------------------------------------- agents (MN-40)
#
# Backend for Agents-design.md §1/§2: an agent is a folder (`agent_registry.
# Agent`) whose `memory/` is registered as an ordinary bank — the same
# `registry.add()` call `api_add_bank` above makes, just without the console's
# confirmation dialog. `agent_registry` owns the storage rules (folder shape,
# launch.json validation, the owns_root delete safety net); this section is
# only the HTTP shape around it, mirroring the bank endpoints' style
# (`_resolve_bank`/`_bank_info` -> `_resolve_agent`/`_agent_info`).


class AgentPreviewRequest(BaseModel):
    root: str


class AgentCreateRequest(BaseModel):
    name: str
    root: str | None = None
    claude_md: str | None = None
    # Adopting a non-empty folder without this set to true gets a 409
    # (`adoption_confirmation_required`) carrying the same preview the console
    # would show before asking — one endpoint, a flag instead of two.
    confirm_adopt: bool = False


class PatchAgentRequest(BaseModel):
    """Editable fields of a registered agent. Omitted means unchanged.

    Mirrors `PatchBankRequest`: `slug`/`root` are absent on purpose — a
    rename changes only the display name (`agent_registry.rename`).
    """

    name: str | None = None


class ClaudeMdRequest(BaseModel):
    content: str


def _resolve_agent(slug: str) -> agent_registry.Agent:
    try:
        return agent_registry.get(slug)
    except agent_registry.AgentNotFound as exc:
        raise ApiError("agent_not_found", str(exc), slug=slug) from exc


def _agent_info(agent: agent_registry.Agent) -> dict:
    """The one agent shape the API returns."""
    bank_id = agent.bank_id
    bank_name = None
    with suppress(BankNotFound):
        bank_name = registry.get(bank_id).name
    try:
        launch = agent_registry.read_launch_config(agent.root)
    except agent_registry.InvalidLaunchConfig as exc:
        # A listing must not go down because one agent's launch.json was
        # hand-edited into something invalid — report it inline instead.
        launch = {"error": str(exc)}
    return {
        "slug": agent.slug,
        "name": agent.name,
        "root": agent.root.as_posix(),
        "owns_root": agent.owns_root,
        "created_at": agent.created_at,
        "bank_id": bank_id,
        "bank_name": bank_name,
        "launch": launch,
    }


@app.get("/api/agents", include_in_schema=False)
def api_agents() -> dict:
    return {"agents": [_agent_info(a) for a in agent_registry.list_agents()]}


@app.post("/api/agents/preview", include_in_schema=False)
def api_agent_preview(req: AgentPreviewRequest) -> dict:
    """Dry-run inspection of a candidate folder. Never writes anything."""
    return agent_registry.preview_adopt(req.root)


@app.post("/api/agents", status_code=201, include_in_schema=False)
def api_create_agent(req: AgentCreateRequest) -> dict:
    name = req.name.strip()
    if not name:
        raise ApiError("bad_request", "'name' cannot be empty")

    if req.root is not None:
        raw = Path(req.root)
        if not raw.is_absolute():
            raise ApiError("bad_request", "потрібен абсолютний шлях", root=req.root)
        resolved = raw.expanduser().resolve()
        preview = agent_registry.preview_adopt(resolved)
        if preview["root_exists"] and not preview["empty"] and not req.confirm_adopt:
            raise ApiError(
                "adoption_confirmation_required",
                f"{resolved} is not empty — confirm adoption to proceed",
                root=req.root,
                preview=preview,
            )
        if preview["root_exists"] and not preview["empty"]:
            try:
                agent = agent_registry.adopt(resolved, name, claude_md=req.claude_md)
            except BankExists as exc:
                raise ApiError("bank_exists", str(exc), root=req.root) from exc
        else:
            try:
                agent = agent_registry.create(name, root=resolved, claude_md=req.claude_md)
            except agent_registry.AgentExists as exc:
                raise ApiError("agent_exists", str(exc), root=req.root) from exc
            except BankExists as exc:
                raise ApiError("bank_exists", str(exc), root=req.root) from exc
    else:
        try:
            agent = agent_registry.create(name, claude_md=req.claude_md)
        except agent_registry.AgentExists as exc:
            raise ApiError("agent_exists", str(exc)) from exc
        except BankExists as exc:
            raise ApiError("bank_exists", str(exc)) from exc

    info = _agent_info(agent)
    hub.publish("bank_added", {"bank": _bank_info(registry.get(agent.bank_id))}, agent.bank_id)
    q = _queue()
    if q is not None:
        with suppress(Exception):
            q.enqueue_bulk(agent.bank_id, trigger="api")
    else:
        log.info("agent %s registered; indexing waits for the queue", agent.slug)
    return info


@app.get("/api/agents/{slug}", include_in_schema=False)
def api_agent(slug: str) -> dict:
    return _agent_info(_resolve_agent(slug))


@app.delete("/api/agents/{slug}", include_in_schema=False)
def api_remove_agent(slug: str) -> dict:
    agent = _resolve_agent(slug)
    agent_registry.delete(agent.slug)
    log.info("removed agent %s (owns_root=%s)", agent.slug, agent.owns_root)
    return {"ok": True}


@app.get("/api/agents/{slug}/launch", include_in_schema=False)
def api_agent_launch(slug: str) -> dict:
    agent = _resolve_agent(slug)
    try:
        return agent_registry.read_launch_config(agent.root)
    except agent_registry.InvalidLaunchConfig as exc:
        raise ApiError("invalid_launch_config", str(exc), slug=slug) from exc


@app.put("/api/agents/{slug}/launch", include_in_schema=False)
def api_agent_launch_save(slug: str, payload: dict = Body(...)) -> dict:
    agent = _resolve_agent(slug)
    try:
        return agent_registry.write_launch_config(agent.root, payload)
    except agent_registry.InvalidLaunchConfig as exc:
        raise ApiError("invalid_launch_config", str(exc), slug=slug) from exc


@app.patch("/api/agents/{slug}", include_in_schema=False)
def api_patch_agent(slug: str, req: PatchAgentRequest) -> dict:
    """Rename an agent. Mirrors `PATCH /api/banks/{id}`'s shape; unlike that
    endpoint, there is no cross-entry name collision to guard against — see
    `agent_registry.rename`."""
    agent = _resolve_agent(slug)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise ApiError("bad_request", "nothing to change", slug=slug)
    try:
        updated = agent_registry.rename(agent.slug, fields["name"])
    except agent_registry.AgentNotFound as exc:
        raise ApiError("agent_not_found", str(exc), slug=slug) from exc
    except ValueError as exc:
        raise ApiError("bad_request", str(exc), slug=slug) from exc
    log.info("renamed agent %s -> %r", updated.slug, updated.name)
    return _agent_info(updated)


@app.get("/api/agents/{slug}/claude-md", include_in_schema=False)
def api_agent_claude_md(slug: str) -> dict:
    agent = _resolve_agent(slug)
    return {"content": agent_registry.read_claude_md(agent.root)}


@app.put("/api/agents/{slug}/claude-md", include_in_schema=False)
def api_agent_claude_md_save(slug: str, req: ClaudeMdRequest) -> dict:
    agent = _resolve_agent(slug)
    agent_registry.write_claude_md(agent.root, req.content)
    return {"content": req.content}


# --------------------------------------------------------- agent chats (MN-43)
#
# Lifecycle only — list/create/get/delete a chat *record*. The live PTY
# process itself is owned by `agent_runtime.py` and is never touched here:
# it spawns lazily on the first WebSocket subscriber
# (`/ws/agents/{slug}/chats/{chat_id}`, below), which is exactly why
# `POST` here is cheap and does not itself start `claude`.


class CreateChatRequest(BaseModel):
    title: str | None = None


def _resolve_chat(slug: str, chat_id: str) -> dict:
    try:
        return agent_registry.get_chat(slug, chat_id)
    except agent_registry.ChatNotFound as exc:
        raise ApiError("chat_not_found", str(exc), slug=slug, chat_id=chat_id) from exc


@app.get("/api/agents/{slug}/chats", include_in_schema=False)
def api_agent_chats(slug: str) -> dict:
    _resolve_agent(slug)
    return {"chats": agent_registry.list_chats(slug)}


@app.post("/api/agents/{slug}/chats", status_code=201, include_in_schema=False)
def api_create_chat(slug: str, req: CreateChatRequest) -> dict:
    _resolve_agent(slug)
    return agent_registry.create_chat(slug, title=req.title)


@app.get("/api/agents/{slug}/chats/{chat_id}", include_in_schema=False)
def api_agent_chat(slug: str, chat_id: str) -> dict:
    _resolve_agent(slug)
    return _resolve_chat(slug, chat_id)


@app.delete("/api/agents/{slug}/chats/{chat_id}", include_in_schema=False)
def api_delete_chat(slug: str, chat_id: str) -> dict:
    _resolve_agent(slug)
    _resolve_chat(slug, chat_id)
    # Best-effort: a live session is stopped before the record and its
    # `chats/<id>/` folder disappear, so a still-running `claude` process
    # never outlives the storage its own history.log was writing into.
    with suppress(Exception):
        agent_runtime.stop_session(chat_id)
    agent_registry.delete_chat(slug, chat_id)
    return {"ok": True}


# ------------------------------------------------- agent <-> catalog links (MN-48)
#
# Attach/detach a catalog entry to an agent's `.mcp.json` / `.claude/skills`
# / `.claude/rules`, materializing write-through at attach/edit/detach time
# (`agent_registry.py`'s module docstring on `links.json` explains why: the
# Claude Code CLI reads those files itself at agent start, mnemo does not
# intercept that read). This section is only the HTTP shape around
# `agent_registry.attach_link`/`update_link`/`detach_link`/`list_links` —
# same style as every other resource in this file.
#
# Editing a catalog entry after it is attached deliberately does NOT
# refresh what was already materialized (pinned-copy semantics, MN-48's
# ticket) — nothing here tries to "fix" that; drift is out of scope by
# design.


class AttachLinkRequest(BaseModel):
    entry_id: str
    name: str
    vars: dict[str, str] = Field(default_factory=dict)


class UpdateLinkRequest(BaseModel):
    """Editable fields of a link. Omitted means unchanged; `vars: {}`
    (present but empty) is a deliberate "clear every var", distinct from
    omitting `vars` entirely — see the filtering below."""

    name: str | None = None
    vars: dict[str, str] | None = None


@app.get("/api/agents/{slug}/links", include_in_schema=False)
def api_agent_links(slug: str) -> dict:
    _resolve_agent(slug)  # 404 before touching anything
    return agent_registry.list_links(slug)


@app.post("/api/agents/{slug}/links/{category}", status_code=201, include_in_schema=False)
def api_attach_link(
    slug: str, category: Literal["mcp", "skill", "rule"], req: AttachLinkRequest
) -> dict:
    _resolve_agent(slug)
    try:
        link = agent_registry.attach_link(slug, category, req.entry_id, req.name, req.vars)
    except catalog.EntryNotFound as exc:
        raise ApiError("catalog_entry_not_found", str(exc), id=req.entry_id) from exc
    except agent_registry.CategoryMismatch as exc:
        raise ApiError("category_mismatch", str(exc), id=req.entry_id) from exc
    except agent_registry.LinkExists as exc:
        raise ApiError("link_exists", str(exc), id=req.entry_id) from exc
    except agent_registry.LinkNameExists as exc:
        raise ApiError(
            "link_name_exists", str(exc), existing_entry_id=exc.existing_entry_id
        ) from exc
    except agent_registry.UnknownLinkVar as exc:
        raise ApiError("unknown_var", str(exc), id=req.entry_id) from exc
    except agent_registry.InvalidLinkName as exc:
        raise ApiError("bad_request", str(exc), id=req.entry_id) from exc
    except agent_registry.LinkPathConflict as exc:
        raise ApiError("path_conflict", str(exc), id=req.entry_id) from exc
    except agent_registry.InvalidSubstitutedConfig as exc:
        raise ApiError("invalid_substituted_config", str(exc), id=req.entry_id) from exc
    log.info("attached %s link %s (%r) to agent %s", category, req.entry_id, link["name"], slug)
    return link


@app.patch("/api/agents/{slug}/links/{category}/{entry_id}", include_in_schema=False)
def api_update_link(
    slug: str, category: Literal["mcp", "skill", "rule"], entry_id: str,
    req: UpdateLinkRequest,
) -> dict:
    _resolve_agent(slug)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise ApiError("bad_request", "nothing to change", slug=slug, id=entry_id)
    try:
        link = agent_registry.update_link(slug, category, entry_id, **fields)
    except catalog.EntryNotFound as exc:
        raise ApiError("catalog_entry_not_found", str(exc), id=entry_id) from exc
    except agent_registry.CategoryMismatch as exc:
        raise ApiError("category_mismatch", str(exc), id=entry_id) from exc
    except agent_registry.LinkNotFound as exc:
        raise ApiError("link_not_found", str(exc), id=entry_id) from exc
    except agent_registry.LinkNameExists as exc:
        raise ApiError(
            "link_name_exists", str(exc), existing_entry_id=exc.existing_entry_id
        ) from exc
    except agent_registry.UnknownLinkVar as exc:
        raise ApiError("unknown_var", str(exc), id=entry_id) from exc
    except agent_registry.InvalidLinkName as exc:
        raise ApiError("bad_request", str(exc), id=entry_id) from exc
    except agent_registry.LinkPathConflict as exc:
        raise ApiError("path_conflict", str(exc), id=entry_id) from exc
    except agent_registry.InvalidSubstitutedConfig as exc:
        raise ApiError("invalid_substituted_config", str(exc), id=entry_id) from exc
    log.info("updated %s link %s on agent %s", category, entry_id, slug)
    return link


@app.delete("/api/agents/{slug}/links/{category}/{entry_id}", include_in_schema=False)
def api_detach_link(
    slug: str, category: Literal["mcp", "skill", "rule"], entry_id: str
) -> dict:
    _resolve_agent(slug)
    try:
        agent_registry.detach_link(slug, category, entry_id)
    except agent_registry.LinkNotFound as exc:
        raise ApiError("link_not_found", str(exc), id=entry_id) from exc
    log.info("detached %s link %s from agent %s", category, entry_id, slug)
    return {"ok": True}


# -------------------------------------------------------- catalog (MN-41)
#
# The general MCP/Skills/Rules registry (`catalog.py`) — a flat, agent-
# agnostic store a human adds entries to by hand, independent of any agent
# (package-manager-cache shaped, per the ticket). `catalog.py` owns the
# storage rules (id/name shape, JSON validation, `mcp`-config dedup); this
# section is only the HTTP shape around it, same style as the bank/agent
# endpoints above (`_resolve_bank`/`_resolve_agent` -> `_resolve_catalog_entry`).
#
# `catalog.py` never imports `agent_registry` (see its module docstring) —
# whether an entry is still referenced by an agent is answered by
# `_catalog_used_by` below, a **guarded call** rather than a hard dependency,
# same shape as `_queue()`'s guarded import ahead of phase 3: a missing
# capability answers "nothing uses this" rather than a 500. MN-48 added the
# finder (`agent_registry.catalog_entry_used_by`) the guard was written for,
# so the guard itself stays — a future capability going away should degrade
# the same way a not-yet-arrived one does, not turn into a 500.


class CreateCatalogEntryRequest(BaseModel):
    category: Literal["mcp", "skill", "rule"]
    name: str
    content: str


class UpdateCatalogEntryRequest(BaseModel):
    """Editable fields of a catalog entry. Omitted means unchanged.

    ``category`` is absent on purpose: it is fixed at creation (see
    `catalog.py`'s module docstring) — changing it after the fact would
    upend the JSON/dedup rules, which apply to `mcp` only.
    """

    name: str | None = None
    content: str | None = None


def _resolve_catalog_entry(entry_id: str) -> catalog.CatalogEntry:
    try:
        return catalog.get(entry_id)
    except catalog.EntryNotFound as exc:
        raise ApiError("catalog_entry_not_found", str(exc), id=entry_id) from exc


def _entry_info(entry: catalog.CatalogEntry) -> dict:
    """The one catalog-entry shape the API returns.

    ``used_by_count`` calls `_catalog_used_by` even though it is defined
    below this function in the file — fine, both are plain module-level
    functions resolved at call time, not at definition time, and every call
    to `_entry_info` happens well after the module has finished loading.
    """
    return {
        "id": entry.id,
        "category": entry.category,
        "name": entry.name,
        "content": entry.content,
        "created_at": entry.created_at,
        "vars": entry.vars,
        "used_by_count": len(_catalog_used_by(entry.id)),
    }


def _catalog_used_by(entry_id: str) -> list[str]:
    """Agent slugs that reference this catalog entry via `links.json` — `[]`
    when `agent_registry.catalog_entry_used_by` is not present (see the
    section banner above for why this is a guarded call, not an import); as
    of MN-48 that finder exists, so this now reports real state."""
    finder = getattr(agent_registry, "catalog_entry_used_by", None)
    if finder is None:
        return []
    try:
        return list(finder(entry_id))
    except Exception:  # noqa: BLE001
        # A future finder's own bug must not turn "delete this entry" into a
        # 500 — treat it the same as "the capability isn't there yet".
        log.exception("catalog_entry_used_by(%r) failed", entry_id)
        return []


@app.get("/api/catalog", include_in_schema=False)
def api_catalog(category: Literal["mcp", "skill", "rule"] | None = None) -> dict:
    return {"entries": [_entry_info(e) for e in catalog.list_entries(category)]}


@app.post("/api/catalog", status_code=201, include_in_schema=False)
def api_create_catalog_entry(req: CreateCatalogEntryRequest) -> dict:
    try:
        entry = catalog.add(req.category, req.name, req.content)
    except catalog.InvalidCatalogEntry as exc:
        raise ApiError("invalid_catalog_entry", str(exc)) from exc
    except catalog.EntryExists as exc:
        raise ApiError(
            "catalog_entry_exists", str(exc), existing_id=exc.existing_id
        ) from exc
    return _entry_info(entry)


@app.get("/api/catalog/{entry_id}", include_in_schema=False)
def api_catalog_entry(entry_id: str) -> dict:
    return _entry_info(_resolve_catalog_entry(entry_id))


@app.patch("/api/catalog/{entry_id}", include_in_schema=False)
def api_patch_catalog_entry(entry_id: str, req: UpdateCatalogEntryRequest) -> dict:
    _resolve_catalog_entry(entry_id)  # 404 before touching anything
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if not fields:
        raise ApiError("bad_request", "nothing to change", id=entry_id)
    try:
        entry = catalog.update(entry_id, **fields)
    except catalog.EntryNotFound as exc:
        raise ApiError("catalog_entry_not_found", str(exc), id=entry_id) from exc
    except catalog.InvalidCatalogEntry as exc:
        raise ApiError("invalid_catalog_entry", str(exc)) from exc
    except catalog.EntryExists as exc:
        raise ApiError(
            "catalog_entry_exists", str(exc), existing_id=exc.existing_id
        ) from exc
    return _entry_info(entry)


@app.delete("/api/catalog/{entry_id}", include_in_schema=False)
def api_remove_catalog_entry(entry_id: str) -> dict:
    entry = _resolve_catalog_entry(entry_id)
    used_by = _catalog_used_by(entry.id)
    if used_by:
        raise ApiError(
            "entry_in_use",
            f"{entry.name!r} is still used by {len(used_by)} agent(s)",
            id=entry.id, agents=used_by,
        )
    catalog.remove(entry.id)
    log.info("removed catalog entry %s (%s)", entry.id, entry.category)
    return {"ok": True}


# ------------------------------------------- filesystem browse (bank picker)
#
# A browser cannot tell a page which folder the user picked. `webkitdirectory`
# yields relative names only, and `showDirectoryPicker()` hands back a handle
# while withholding the path *on purpose* — the absolute path is treated as
# private to the machine. So the console's "add a bank" picker cannot come from
# the page; the walking has to happen on this side, and this is that endpoint.
#
# It is deliberately the smallest thing that answers the two questions a person
# asks while picking a bank root: what is inside this folder, and is there any
# markdown here to index. It lists directory *names*, never file names, never
# file contents.

# Directories returned for one listing. A folder with more subdirectories than
# this is not something anybody browses by clicking — the path field is the
# answer there, so the listing says it was cut rather than pretending.
_FS_ENTRY_LIMIT = 500

# How long the `.md` count may take before it reports a floor instead of a
# total. A budget in seconds rather than in directories because latency is the
# thing being protected: one click in the picker must stay a click. A projects
# folder blows through any plausible directory ceiling in milliseconds and then
# reports a number that is not just approximate but wrong by two orders of
# magnitude, so the ceiling stays only as a backstop against a pathological
# tree of empty directories.
_MD_SCAN_SECONDS = 0.4
_MD_SCAN_CAP = 20000


def _fs_roots() -> list[dict]:
    """Places to start browsing from: drive letters, or ``/``.

    Windows drives come from the kernel's bitmask rather than from probing
    ``A:\\`` … ``Z:\\`` one at a time: probing spins up removable media and
    blocks for seconds on a disconnected network mapping.
    """
    if os.name != "nt":
        return [{"name": "/", "path": "/"}]
    mask = 0
    try:
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - no drive list must never break browsing
        log.debug("GetLogicalDrives unavailable; browsing without drive roots")
    roots = []
    for i in range(26):
        if mask & (1 << i):
            letter = chr(ord("A") + i)
            roots.append({"name": f"{letter}:", "path": f"{letter}:/"})
    return roots


def _count_md_tree(root: Path) -> tuple[int, bool]:
    """`.md` anywhere under ``root``, honouring the indexer's own excludes.

    Returns ``(count, capped)``, where ``capped`` means "at least this many"
    — the walk ran out of its time budget. Counting one level deep would be
    cheaper and wrong: a bank root's markdown usually sits in ``docs/`` or
    ``logs/``, so a folder holding hundreds of notes would report zero and read
    as "empty, wrong folder". The same ``DEFAULT_EXCLUDE`` the walk uses applies
    here, or the number would promise files the index is never going to hold.
    """
    patterns = _compile_excludes(config.DEFAULT_EXCLUDE)
    deadline = time.monotonic() + _MD_SCAN_SECONDS
    found = scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        try:
            rel_dir = here.relative_to(root).as_posix()
        except ValueError:  # a symlink walked us out of the tree
            dirnames[:] = []
            continue
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [
            d
            for d in dirnames
            if not _excluded(f"{rel_dir}/{d}" if rel_dir else d, patterns, is_dir=True)
        ]
        for name in filenames:
            if not name.endswith(".md"):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if not _excluded(rel, patterns, is_dir=False):
                found += 1
        scanned += 1
        if scanned >= _MD_SCAN_CAP or time.monotonic() >= deadline:
            return found, True
    return found, False


def _registered_roots() -> dict[str, str]:
    """Resolved bank root → bank name, so the picker can say "already a bank"."""
    out: dict[str, str] = {}
    for bank in registry.load():
        with suppress(OSError):
            out[bank.root.expanduser().resolve().as_posix()] = bank.name
    return out


def _memory_dir_for(target: Path) -> Path:
    """Where the "create structure" checkbox's `.claude/memory` would land
    for `target`, without doubling a `.claude` the user already picked.

    Three shapes reach here: `target` already IS `<x>/.claude/memory` (the
    checkbox is irrelevant then — the caller's own eligibility check handles
    that), `target` already IS `<x>/.claude` (one level short — only
    `memory` is missing), or `target` is an ordinary folder (both `.claude`
    and `memory` are missing).
    """
    if target.name.lower() == "memory" and target.parent.name.lower() == ".claude":
        return target
    if target.name.lower() == ".claude":
        return target / "memory"
    return target / ".claude" / "memory"


@app.get("/api/fs/dirs", include_in_schema=False)
def api_fs_dirs(path: str | None = None) -> dict:
    """Sub-directories of one directory (§9.5). Defaults to the user's home."""
    raw = (path or "").strip()
    target = Path(raw).expanduser() if raw else Path.home()
    if not target.is_absolute():
        raise ApiError("bad_request", "потрібен абсолютний шлях", path=raw)
    try:
        target = target.resolve()
        listable = target.is_dir()
        exists = listable or target.exists()
    except OSError as exc:
        # A dead network mapping raises here rather than answering False.
        raise ApiError("bad_request", f"шлях недоступний: {exc}", path=raw) from exc
    if not listable:
        # "gone" and "that is a file" are different mistakes and get different
        # sentences: one is a typo to fix, the other is a folder to go up from.
        raise ApiError(
            "bad_request",
            f"{target.as_posix()} — це файл, а не тека" if exists
            else f"немає такої теки: {target.as_posix()}",
            path=raw,
        )

    registered = _registered_roots()
    entries: list[dict] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for item in it:
                try:
                    # Symlinks and Windows junctions are followed on purpose:
                    # this never recurses, so a loop costs nothing, while
                    # skipping them would hide ordinary folders.
                    if not item.is_dir():
                        continue
                except OSError:
                    continue
                if len(entries) >= _FS_ENTRY_LIMIT:
                    truncated = True
                    break
                as_posix = Path(item.path).as_posix()
                entries.append({
                    "name": item.name,
                    "path": as_posix,
                    "registered": registered.get(as_posix),
                })
    except PermissionError as exc:
        raise ApiError("bad_request", "нема доступу до цієї теки", path=raw) from exc
    except OSError as exc:
        raise ApiError("bad_request", f"тека не читається: {exc}", path=raw) from exc

    entries.sort(key=lambda e: e["name"].lower())
    md, md_capped = _count_md_tree(target)
    parent = target.parent
    memory_dir = _memory_dir_for(target)
    return {
        "path": target.as_posix(),
        "display": str(target),
        # `Path("C:/").parent` is itself; a root has nowhere up to go.
        "parent": None if parent == target else parent.as_posix(),
        "home": Path.home().as_posix(),
        "roots": _fs_roots(),
        "registered": registered.get(target.as_posix()),
        "md": md,
        "md_capped": md_capped,
        "entries": entries,
        "truncated": truncated,
        # Where the "create structure" checkbox would put `.claude/memory`
        # for this exact `target` (never doubles a `.claude` already picked
        # — `_memory_dir_for`), and whether it's already there.
        "memory_dir": memory_dir.as_posix(),
        "has_claude_memory": memory_dir != target and memory_dir.is_dir(),
    }


@app.post("/api/reindex", status_code=202, include_in_schema=False)
def api_reindex(req: ReindexRequest) -> dict:
    # `require_enabled=False`, so a **frozen** bank can be reindexed on
    # request: the freeze stops the watcher, not the owner. The queue makes
    # the same distinction by trigger (`workqueue._may_run`), and a disabled
    # bank is still refused — by `searchable`, one line down.
    bank = _resolve_bank(req.bank, require_enabled=False)
    if not bank.searchable:
        raise ApiError(
            "bank_not_found", f"bank {bank.name!r} is disabled", ref=req.bank
        )
    q = _require_queue()
    if req.path:
        rel = _safe_relpath(bank, req.path)
        task_ids = [
            q.enqueue_file(bank.id, rel, priority=q.Priority.NORMAL, trigger="api")
        ]
    else:
        task_ids = [q.enqueue_bulk(bank.id, trigger="api", rebuild=req.full)]
    return {"ok": True, "task_ids": task_ids, "queued": _queued(bank.id)}


def _safe_relpath(bank: Bank, path: str) -> str:
    """Bank-relative POSIX path, or ``path_outside_bank``."""
    candidate = Path(path)
    root = bank.root.expanduser().resolve()
    absolute = (root / candidate) if not candidate.is_absolute() else candidate
    try:
        rel = absolute.resolve().relative_to(root)
    except ValueError as exc:
        raise ApiError("path_outside_bank", f"{path!r} is outside {bank.name}",
                       path=path) from exc
    return rel.as_posix()


@app.get("/api/tree", include_in_schema=False)
def api_tree(
    bank: str,
    links: bool = False,
    depth: int = 0,
) -> dict:
    b = _resolve_bank(bank, require_enabled=False)
    root = b.root.expanduser().resolve()
    if not root.is_dir():
        raise ApiError("root_not_found", f"{root.as_posix()} is gone", ref=bank)

    indexed: dict[str, dict] = {}
    with _bank_conn(b) as conn:
        if conn is not None:
            # Counts come from an index-only GROUP BY; only rows that actually
            # carry a heading are materialised, not every chunk in the bank.
            for row in conn.execute(
                "SELECT path, count(*) AS n FROM chunks GROUP BY path"
            ):
                indexed[row["path"]] = {"chunks": row["n"], "headings": []}
            for row in conn.execute(
                "SELECT path, heading FROM chunks "
                "WHERE heading IS NOT NULL AND heading != '' "
                "ORDER BY path, chunk_index"
            ):
                entry = indexed.get(row["path"])
                if entry is not None and row["heading"] not in entry["headings"]:
                    entry["headings"].append(row["heading"])

    patterns = _compile_excludes(b.exclude)
    files = 0
    tree: dict[str, Any] = {"name": "", "type": "dir", "path": "", "children": []}
    nodes: dict[str, dict] = {"": tree}
    # A dir whose descent was cut short by `depth` (not by excludes) may hold
    # real .md files the walk never reached — pruning must not mistake
    # "not looked at" for "empty" and delete it.
    truncated: set[str] = set()

    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        rel_dir = here.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        level = 0 if not rel_dir else rel_dir.count("/") + 1
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not _excluded(
                f"{rel_dir}/{d}" if rel_dir else d, patterns, is_dir=True
            )
        )
        if depth and level >= depth:
            if dirnames:
                truncated.add(rel_dir)
            dirnames[:] = []
        parent = nodes.get(rel_dir)
        if parent is None:
            continue
        for name in dirnames:
            rel = f"{rel_dir}/{name}" if rel_dir else name
            child: dict[str, Any] = {
                "name": name, "type": "dir", "path": rel, "children": [],
            }
            nodes[rel] = child
            parent["children"].append(child)
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if _excluded(rel, patterns, is_dir=False):
                continue
            info = indexed.get(rel)
            leaf: dict[str, Any] = {
                "name": name,
                "type": "file",
                "path": rel,
                "size": (here / name).stat().st_size,
                "indexed": info is not None,
                "chunks": info["chunks"] if info else 0,
                "headings": info["headings"] if info else [],
            }
            if links:
                leaf["links"] = _md_links(here / name)
            parent["children"].append(leaf)
            files += 1

    # Directories before files, each group by name (§9.5).
    for node in nodes.values():
        node["children"].sort(key=lambda c: (c["type"] != "dir", c["name"]))

    # Bottom-up: a dir's own file count isn't known until its subtree is
    # walked, so pruning empty branches has to happen after the os.walk
    # build, not during it.
    _prune_empty_dirs(tree, truncated)
    dirs = _count_dirs(tree)

    return {
        "bank_id": b.id,
        "root": root.as_posix(),
        "files": files,
        "dirs": dirs,
        "tree": tree,
        # Relpaths queued or in flight right now (kind='file'/'prune') — a
        # page opened mid-index highlights these without waiting for the
        # first `file_queued`/`index_done` WS event to arrive.
        "pending": _pending_paths(b.id),
    }


def _prune_empty_dirs(node: dict, truncated: set[str]) -> bool:
    """Drop dir children with no ``.md`` anywhere in their subtree. Returns
    whether ``node`` itself should be kept.

    A dir in ``truncated`` had its own descent cut short by the ``depth``
    limit rather than by excludes, so an empty ``children`` there means
    "not looked at", not "empty" — always kept.
    """
    if node["type"] != "dir":
        return True
    if node["path"] in truncated:
        return True
    kept = [
        child for child in node["children"] if _prune_empty_dirs(child, truncated)
    ]
    node["children"] = kept
    return bool(kept)


def _count_dirs(node: dict) -> int:
    return sum(
        1 + _count_dirs(child) for child in node["children"] if child["type"] == "dir"
    )


_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+\.md)[^)]*\)")


def _md_links(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen: list[str] = []
    for match in _MD_LINK.finditer(text):
        target = match.group(1)
        if target not in seen:
            seen.append(target)
    return seen


def _as_indexed(raw: bytes) -> str:
    """Decode a ``.md`` exactly the way the indexer read it.

    ``start_char`` / ``end_char`` are **Python code-point offsets** into this
    string — not bytes, not UTF-16 units. Two conversions have to match the
    indexer's ``Path.read_text(encoding="utf-8")`` or every boundary the
    console draws is silently wrong:

    * **UTF-8 decode**, so one Cyrillic letter is one position, not two;
    * **universal newlines**, so a CRLF file (the norm on Windows) counts one
      position per line break. Skipping this shifts every chunk after the
      first line by one, and the drawing still *looks* plausible.

    The invariant callers may rely on: ``text[start_char:end_char]`` equals
    the indexed chunk under plain Python slicing.
    """
    return raw.decode("utf-8", errors="replace").replace(
        "\r\n", "\n"
    ).replace("\r", "\n")


@app.get("/api/file", include_in_schema=False)
def api_file(bank: str, path: str) -> dict:
    b = _resolve_bank(bank, require_enabled=False)
    rel = _safe_relpath(b, path)
    if not rel.endswith(".md"):
        raise ApiError("bad_request", "only .md files are served", path=path)
    absolute = b.root.expanduser().resolve() / rel
    if not absolute.is_file():
        raise ApiError("file_not_found", f"{rel} is not in {b.name}", path=rel)
    size = absolute.stat().st_size
    if size > FILE_MAX_BYTES:
        raise ApiError(
            "bad_request",
            f"{rel} is {size} bytes, over the {FILE_MAX_BYTES} limit",
            path=rel,
        )
    raw = absolute.read_bytes()
    text = _as_indexed(raw)

    chunks: list[dict] = []
    indexed = False
    with _bank_conn(b) as conn:
        if conn is not None:
            rows = store.chunk_map(conn, rel)
            indexed = store.get_file_row(conn, rel) is not None
            chunks = [
                {
                    "chunk_uid": r["chunk_uid"],
                    "chunk_index": r["chunk_index"],
                    "heading": r["heading"],
                    "start_char": r["start_char"],
                    "end_char": r["end_char"],
                }
                for r in rows
            ]

    return {
        "bank_id": b.id,
        "path": rel,
        "size": size,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "indexed": indexed,
        "text": text,
        "chunks": chunks,
    }


@app.get("/api/status", include_in_schema=False)
def api_status() -> dict:
    identity, provider_error = _provider_identity(None)
    try:
        # health() is cheap and side-effect free by contract — for `api` it
        # checks configuration and deliberately makes no request, so a status
        # command never costs money or burns a rate limit.
        reachable = bool(get_provider().health()) if identity else False
        embed = {
            "reachable": reachable,
            "host": getattr(config, "EMBED_HOST", None),
            "port": getattr(config, "EMBED_PORT", None),
            # What was actually probed. Under `local` "reachable" is a live
            # resident on that host/port; under `api` it is configuration only
            # (health() makes no request by contract), and a client that
            # cannot tell them apart renders an unconfigured endpoint as a
            # dead process — or worse, a working one as "DOWN".
            "kind": identity.get("name") or "unknown",
        }
    except Exception as exc:  # noqa: BLE001
        embed = {"reachable": False, "error": str(exc)}
    if provider_error:
        embed["error"] = provider_error
    return {
        "service": {
            "version": SERVICE_VERSION,
            "pid": os.getpid(),
            # Both halves of the address, because the console builds the
            # config snippets a project pastes and cannot read the binding
            # from anywhere else. `location.hostname` is not the same fact:
            # it is how *this browser* reached the service, which on a
            # non-loopback bind is one of several working answers.
            "host": API_HOST,
            "port": API_PORT,
            "started_at": _started_iso,
            "uptime_s": round(time.time() - _started_at, 1),
            "provider": identity.get("name"),
            "provider_model": identity.get("model"),
            "provider_dim": identity.get("dim"),
            "provider_key": identity.get("key"),
            "provider_error": provider_error,
            "priority_enabled": os.environ.get("MNEMO_QUEUE_PRIORITY", "1") != "0",
            "embed": embed,
        },
        "queue": _queue_snapshot_json(),
        "banks": [_bank_info(b) for b in registry.load()],
    }


@app.post("/api/shutdown", include_in_schema=False)
async def api_shutdown() -> dict:
    """Ask the running server to stop gracefully (MN-11).

    Windows has no signal uvicorn's own SIGTERM handler can catch, so
    ``service stop`` reaches this over loopback HTTP instead. Setting
    ``should_exit`` here is exactly what that handler does on POSIX — the
    existing ``lifespan`` teardown (``_shutdown()``: watcher, queue, journal)
    already runs correctly whenever the flag flips, so nothing about that
    path changes.

    Scheduled via ``call_soon`` rather than set synchronously: the response
    must reach the caller before uvicorn starts tearing the server down, or
    the caller (``service_ctl``) would see the connection drop instead of a
    reply.
    """
    server = getattr(app.state, "uvicorn_server", None)
    if server is None:
        raise ApiError(
            "internal",
            "no uvicorn server registered for graceful shutdown "
            "(run() was not used to start this process)",
        )
    asyncio.get_running_loop().call_soon(setattr, server, "should_exit", True)
    return {"ok": True}


@app.get("/api/doctor", include_in_schema=False)
def api_doctor() -> dict:
    """Structured machine diagnostics — the same facts `mnemo doctor` prints.

    The service describes itself rather than making an HTTP request back into
    its own loopback socket. Everything else comes from `diagnostics.collect`,
    which is the single source shared with the CLI. No token value and no
    external embedding request crosses this endpoint.
    """
    from . import diagnostics, service_ctl

    launcher = (service_ctl.read_identity() or {}).get("pid")
    try:
        bank_count: int | None = len(registry.load())
    except Exception:  # noqa: BLE001 - collect() reports the registry error
        bank_count = None
    backend = {
        "up": True,
        "url": f"http://{API_HOST}:{API_PORT}",
        "scope": "machine_port",
        "error": None,
        "serving_pid": os.getpid(),
        "launcher_pid": launcher,
        "banks": bank_count,
        "queue_depth": _queued(),
    }
    # `_configured_token()`, not `api_token()`: this reports whether `/api`
    # itself is gated, and `api_token()` would answer that question by
    # minting the very token it's being asked about (it exists for
    # `/mcp-admin`/`/mcp-tools`, which always want one — `/api` doesn't, by
    # default).
    configured = _configured_token()
    if configured is None:
        token = {"present": False, "source": None, "where": None, "scope": None}
    else:
        env_token = bool((os.environ.get("MNEMO_API_TOKEN") or "").strip())
        if env_token:
            token_source, token_where, token_scope = (
                "env", "MNEMO_API_TOKEN", "machine"
            )
        else:
            token_source, token_where, token_scope = (
                "state_file", token_file().as_posix(), "engine_home"
            )
        token = {
            "present": True,
            "source": token_source,
            "where": token_where,
            "scope": token_scope,
        }
    # Separate from `present`: a token can exist on disk (minted by an
    # unrelated `/mcp-admin`/`/mcp-tools` call) without `/api` requiring it
    # (MN-19) — `present` answers "does a token exist", this answers "does
    # `/api` actually check for one right now".
    token["login_required"] = _api_gated()
    return diagnostics.collect(backend=backend, token=token)


@app.post("/api/clean-orphans", include_in_schema=False)
def api_clean_orphans(req: CleanOrphansRequest) -> dict:
    """Delete only orphan ids shown to and confirmed by the caller."""
    from . import diagnostics

    if not req.ids:
        raise ApiError("bad_request", "expected at least one orphan index id")
    try:
        return diagnostics.delete_orphans(req.ids)
    except diagnostics.OrphanCleanupRefused as exc:
        raise ApiError("orphan_cleanup_refused", str(exc)) from exc


@app.get("/api/settings", include_in_schema=False)
def api_settings() -> dict:
    """Machine settings, each with the value AND where it came from.

    The origin is not decoration. Precedence is environment > file, so a
    value the console stored can be inert, and a form that cannot say
    "overridden by MNEMO_PROVIDER" shows a field that silently does nothing
    when saved.
    """
    resolved = settings.effective()
    return {
        "path": str(settings.settings_file()),
        "exists": settings.settings_file().exists(),
        "settings": {
            key: {
                "value": item.value,
                "source": item.source,
                "env_var": item.env_var,
                "overridden": item.overridden,
            }
            for key, item in resolved.items()
        },
        # Shown, never editable: the console reaches the service through this
        # port and every project's `.mcp.json` holds it, so changing it from
        # a form would cut the page off from its own backend and break wiring
        # the form cannot see. It is an installer-level decision.
        "readonly": {"api_host": API_HOST, "api_port": API_PORT},
        # Backends and their models, so the form is a choice rather than four
        # free-text fields. It carries each model's prefixes and width, which
        # is what stops "point `api` at e5 and forget the markers" from being
        # possible at all (`presets`).
        "presets": presets.as_json(),
    }


@app.put("/api/settings", include_in_schema=False)
def api_settings_save(payload: dict = Body(...)) -> dict:
    """Store settings and make the new provider active for subsequent work.

    `forget_providers()` below drops every handle that snapshots url/model/dim.
    An in-flight file keeps the provider it opened with; the next file sees the
    new key, refuses to mix vector spaces and queues a rebuild. The indexes are
    therefore stale — and explicitly reported as such — but the service itself
    does not need a restart.
    """
    doc: dict = {}
    if "provider" in payload:
        chosen = str(payload["provider"] or "").strip().lower()
        if chosen not in ("local", "api"):
            raise ApiError("bad_request",
                           f"unknown provider {chosen!r} (known: local, api)")
        doc["provider"] = chosen
    if "auto_update" in payload:
        doc["auto_update"] = bool(payload["auto_update"])
    if "require_login" in payload:
        doc["require_login"] = bool(payload["require_login"])
    api_in = payload.get("api")
    if isinstance(api_in, dict):
        api_doc: dict = dict(settings.load().get("api") or {})
        # `passage_prefix`/`query_prefix` are accepted but rarely sent: the
        # catalogue supplies them from the model name. Storing "" is a real
        # choice (a catalogued model whose markers we got wrong), so an empty
        # string is written rather than skipped.
        for key in ("url", "model", "key", "passage_prefix", "query_prefix"):
            if key in api_in:
                api_doc[key] = str(api_in[key] or "")
        if "dim" in api_in:
            try:
                api_doc["dim"] = max(0, int(api_in["dim"] or 0))
            except (TypeError, ValueError):
                raise ApiError("bad_request",
                               "dim must be a whole number") from None
        if "timeout" in api_in:
            try:
                api_doc["timeout"] = float(api_in["timeout"] or 60.0)
            except (TypeError, ValueError):
                raise ApiError("bad_request",
                               "timeout must be a number") from None
        doc["api"] = api_doc
    if not doc:
        raise ApiError(
            "bad_request",
            "expected 'provider', 'auto_update', 'require_login' and/or 'api'",
        )

    settings.save(doc)
    # A cached provider instance holds the url/model/dim it was built with,
    # so an edit that was meant to replace it would otherwise be invisible
    # until a restart even where a restart is not needed.
    forget_providers()
    # If this save just turned the gate on, hand back the service token now:
    # the caller reaching this point was authorized under whatever gate was
    # in effect BEFORE this save, and the console needs the token in hand to
    # show the user before the gate closes behind them.
    extra: dict[str, Any] = {}
    if settings.require_login():
        extra["service_token"] = api_token()
    return {"ok": True, "path": str(settings.settings_file()),
            "restart_required": False, **extra, **api_settings()}


# A `warmup --force` we spawned, tracked by PID so a page reload (or a
# second console tab) still sees it in progress. Module-level and
# single-slot: only one download can be in flight at a time, which is also
# what `download_in_progress` refuses against.
_download: dict[str, Any] = {"pid": None, "started_at": None, "failed": False}


def _download_status() -> dict:
    """Reconcile `_download` against reality, then report it.

    Lazy rather than a polling thread: the console already refetches
    `/api/embed/state` every few seconds while the button is disabled, so a
    check made right here catches the transition just as fast for a lot less
    code. Once the tracked PID has exited, the model being cached is success
    (nothing further to flag) and it not being cached is the only failure
    signal a subprocess we did not wait on can give us.
    """
    from .embedder import is_model_cached
    from .service_ctl import _pid_alive

    pid = _download.get("pid")
    if pid is not None and not _pid_alive(pid):
        _download["pid"] = None
        if not is_model_cached():
            _download["failed"] = True
    return {"active": _download.get("pid") is not None,
            "failed": bool(_download.get("failed"))}


@app.get("/api/embed/state", include_in_schema=False)
def api_embed_state() -> dict:
    """What the active embedding backend is holding in memory.

    Its own endpoint rather than a field on `/api/status`, for the reason
    `/api/autostart` is separate: answering can cost an HTTP round trip to
    Ollama, and the console refetches status on every indexing event. This
    is read when the settings screen opens.
    """
    from . import embedctl

    return {**embedctl.state(), "download": _download_status()}


@app.post("/api/embed/download", include_in_schema=False)
def api_embed_download() -> dict:
    """Spawn `warmup --force` to cache the model, windowless and detached.

    Coarse status only (idle / downloading / ready / failed) by explicit
    choice — no byte-level progress, no new IPC. `warmup()` is synchronous
    and would otherwise load the model into the FastAPI process itself,
    right next to the resident that is supposed to hold it.
    """
    from . import embedctl
    from .service_ctl import spawn_detached, windowless_python

    if embedctl.state().get("cached"):
        raise ApiError("already_cached", "the model is already cached — "
                        "nothing to download")
    if _download_status()["active"]:
        raise ApiError("download_in_progress",
                        "a download is already running", pid=_download["pid"])

    engine_root = Path(__file__).resolve().parent.parent
    cmd = [windowless_python(), "-m", "src.cli", "warmup", "--force"]
    pid = spawn_detached(cmd, cwd=engine_root)
    _download["pid"] = pid
    _download["started_at"] = time.time()
    _download["failed"] = False
    return {"started": True}


def _embed_action(action, *, guard_queue: bool) -> dict:
    """Shared shell for unload/load.

    `guard_queue` gates the busy-queue refusal, not the action itself: `load`
    is a probe through `embed_server.py`'s own `_QUERY_LANE`, isolated from
    the worker's `_BATCH_LANE` there, so it never actually queues behind a
    bulk embed and has nothing to be refused over (MN-20). `unload` still
    goes through the same backend the worker embeds through, so pulling the
    model out from under it remains genuinely unsafe — that guard is unchanged.
    """
    from . import embedctl

    if guard_queue:
        snapshot = _queue_snapshot_json()
        depth = int(snapshot.get("depth") or 0)
        current = snapshot.get("current")
        if depth or current:
            # Refused, not queued behind the work. The worker embeds through
            # the same backend, so pulling the model out from under it raises
            # `EmbeddingUnavailable` mid-file and leaves the bank half-indexed
            # — a cost paid later, by someone who will not connect it to a
            # button pressed now.
            #
            # `depth` alone undercounts: it is the QUEUED backlog and does
            # not include the one file actively in flight, so `current`
            # truthy with `depth == 0` is the common case, not an edge one —
            # a message that only ever cited `depth` read as "0 pending"
            # while also claiming "still working", which is exactly the
            # contradiction a caller like the console would otherwise have
            # to explain away on its own.
            if current and not depth:
                detail = ("a file is being embedded through this backend "
                           "right now — wait for it to finish")
            else:
                detail = (f"the queue is still working ({depth} task(s) "
                           f"pending) — the worker embeds through this "
                           f"backend, so wait for it to drain")
            raise ApiError("embed_busy", detail, depth=depth)
    try:
        return action()
    except embedctl.EmbedControlUnavailable as exc:
        raise ApiError("embed_control_failed", str(exc)) from exc


@app.post("/api/embed/unload", include_in_schema=False)
def api_embed_unload() -> dict:
    """Release the memory the backend is holding. NOT an off switch —
    the next search or indexed file brings the model back, paying ~7-8 s."""
    from . import embedctl

    return _embed_action(embedctl.unload, guard_queue=True)


@app.post("/api/embed/load", include_in_schema=False)
def api_embed_load() -> dict:
    """Bring the model back with a probe embedding, which also proves the
    backend answers and at what width.

    Never refused for a busy queue (MN-20): the probe runs on its own query
    lane in `embed_server.py`, isolated from the worker's bulk-embed lane.
    """
    from . import embedctl

    return _embed_action(embedctl.load, guard_queue=False)


@app.get("/api/autostart", include_in_schema=False)
def api_autostart() -> dict:
    """Is the service registered to start at logon.

    Its own endpoint rather than a field in `/api/status`: answering costs a
    `schtasks` (or `systemctl`) subprocess, ~45 ms measured, and the console
    refetches status on every indexing event. That is a real cost paid
    constantly for a fact that changes when somebody deliberately changes it,
    so it is fetched when the settings screen opens instead.
    """
    from . import autostart

    return autostart.state()


@app.post("/api/autostart", include_in_schema=False)
def api_autostart_set(payload: dict = Body(...)) -> dict:
    """Register or remove the logon entry.

    Returns the state as re-read afterwards, not the state we intended: the
    registration can fail (a missing launcher, a policy that forbids the task)
    and a console that ticked its own checkbox on an optimistic reply would
    then show autostart as on while nothing was registered.
    """
    from . import autostart

    if "enabled" not in payload:
        raise ApiError("bad_request", "expected 'enabled'")
    want = bool(payload["enabled"])
    if want:
        autostart.enable()
    else:
        autostart.disable()

    # The verdict is the re-read, not the exit code. `disable()` reports
    # EXIT_ABSENT when there was nothing to remove — a failure by its own
    # numbering, and exactly the outcome the caller asked for. What the caller
    # wants to know is where the machine ended up, and only this answers that.
    now = autostart.state()
    if bool(now.get("enabled")) is not want:
        # The underlying helpers print their reason to a stdout that is
        # DEVNULL under the windowless service, so there is nothing more
        # specific to pass on than the fact that it did not take.
        raise ApiError(
            "autostart_failed",
            ("could not register the logon entry — check that the engine "
             "launcher exists and that policy allows the task"
             if want else
             "could not remove the logon entry"),
        )
    return now


# ---------------------------------------------------------- self-update (M)
#
# Step 9 of .claude/memory/topics/engine-self-update-design.md. Three
# endpoints: a read-only aggregate, a synchronous check, and an apply that
# returns immediately and does the real work (stage, then hand off to the
# detached `update-apply` CLI, step 8) on a background thread.
#
# "Ready to apply" is deliberately read the same way step 8's CLI reads it
# (accepted as the contract for this step, not reinvented here):
# `last_check.update_available` plus a `versions/<tag>/VERSION` marker that
# matches `last_check.latest_tag`. No separate "staged" field exists or is
# added — a second field recording the same fact would just be one more
# thing that could disagree with the marker on disk.

# In-process view of the CURRENT apply cycle this API process is running,
# for GET /api/update/status to merge with what is on disk. Deliberately
# NOT persisted anywhere: it exists only because this process itself is the
# one background-staging a release, and it dies (see below) the moment
# `update-apply` calls `service_ctl.stop()` on it — so by construction there
# is nothing here worth surviving a restart.
_apply_progress: dict[str, Any] = {
    "state": "idle", "tag": None, "step": None, "detail": None, "error": None,
    "started_at": None, "finished_at": None, "trigger": None,
}
# Wall-clock time of the last _apply_progress mutation — see _apply_view's
# docstring for why freshness, not "which side is idle", is what decides
# which one GET /api/update/status trusts.
_apply_progress_touched_at = 0.0


# Unattended auto-apply's pending countdown (block M extension). Also
# in-process only, same reasoning as `_apply_progress` above: it exists only
# to let a human watching the console see and cancel a countdown before it
# fires, and once it settles (fired or confirmed) the real work continues
# through `_begin_apply`/`_run_staged_apply`, which is what persists.
#
# The dict's mere presence IS the "pending" state -- no separate boolean to
# go out of sync with it. Guarded by its own lock (unlike `_apply_progress`,
# which has none): a timer firing and a confirm click can land within
# milliseconds of each other, and only one of them may win.
_auto_pending: dict[str, Any] | None = None
_auto_pending_timer: threading.Timer | None = None
_auto_pending_lock = threading.Lock()


def _touch_apply_progress(**fields: Any) -> None:
    global _apply_progress_touched_at
    _apply_progress.update(fields)
    _apply_progress_touched_at = time.time()


def maybe_begin_auto_apply(tag: str) -> None:
    """Arm a countdown that auto-applies ``tag`` unless something intervenes.

    Called by ``engine_update.start_checker``'s background loop once a tick
    finds an eligible tag (``engine_update.auto_eligible_tag()``). No-op if
    a countdown is already pending, or if an apply is already under way
    (staging/switching/etc, per ``_apply_progress``) -- a countdown never
    stacks on top of work already in progress.

    Only flips in-memory state and starts a short-lived ``threading.Timer``
    before returning; the actual multi-minute staging work happens later,
    off this call, when that timer fires (``_fire_auto_pending``) or a
    confirm arrives (``POST /api/update/auto/confirm``) -- both funnel into
    :func:`_settle_auto_pending`, which is where ``_begin_apply`` actually
    runs. Safe to call from the checker's own background thread on its
    normal tick cadence for exactly that reason: nothing here blocks.
    """
    global _auto_pending, _auto_pending_timer
    with _auto_pending_lock:
        if _auto_pending is not None:
            return
        if _apply_progress["state"] not in ("idle", "done", "failed", "rolled_back"):
            return
        started_at = _now_iso()
        deadline = (
            datetime.now(timezone.utc).astimezone()
            + timedelta(seconds=config.UPDATE_AUTO_APPLY_COUNTDOWN_S)
        ).isoformat(timespec="milliseconds")
        _auto_pending = {"tag": tag, "started_at": started_at, "deadline": deadline}
        timer = threading.Timer(
            config.UPDATE_AUTO_APPLY_COUNTDOWN_S, _fire_auto_pending, args=(tag,)
        )
        timer.daemon = True
        _auto_pending_timer = timer
        timer.start()

    hub.publish(
        "update_auto_pending",
        {
            "phase": "started", "tag": tag, "deadline": deadline,
            "seconds": config.UPDATE_AUTO_APPLY_COUNTDOWN_S,
        },
        None,
    )


def _settle_auto_pending(tag: str) -> None:
    """Cancel the countdown (idempotent) and hand off to ``_begin_apply``
    with ``trigger="auto"``.

    Shared by the timer firing on its own (:func:`_fire_auto_pending`) and
    ``POST /api/update/auto/confirm`` arriving first -- whichever gets the
    lock first clears ``_auto_pending`` and proceeds; the other sees it
    already ``None`` and returns having done nothing. This is the race the
    resolved flag calls out explicitly: a confirm click and a timer firing
    within milliseconds of each other must not both start an apply.
    """
    global _auto_pending, _auto_pending_timer
    with _auto_pending_lock:
        if _auto_pending is None:
            return
        timer = _auto_pending_timer
        _auto_pending = None
        _auto_pending_timer = None
    if timer is not None:
        timer.cancel()
    _begin_apply(tag, trigger="auto")


def _fire_auto_pending(tag: str) -> None:
    """The armed ``threading.Timer``'s own callback: the countdown elapsed
    with nobody clicking Cancel (a Confirm click funnels through the same
    settle path and would already have cleared ``_auto_pending`` by the
    time this runs, in which case :func:`_settle_auto_pending` is a no-op).

    Guards against a ``stale_target``/``update_in_progress`` ``ApiError``
    from ``_begin_apply`` escaping unhandled into the Timer's own thread --
    the same "never let a background callback raise into a void" tolerance
    every other self-update background path in this codebase already
    applies (``engine_update``'s checker loop, ``_run_staged_apply``'s own
    ``except`` clauses).
    """
    try:
        _settle_auto_pending(tag)
    except Exception:  # noqa: BLE001 - background timer callback, never raise into it
        log.exception("auto-apply countdown for %s failed to settle", tag)


def _spawn_update_apply_breakaway(argv: list[str], cwd: Path) -> None:
    """Spawn ``update-apply`` so it survives calling ``service_ctl.stop()``
    on ITS OWN SPAWNER — the backend running this function.

    **Found and proven live (step 9), not reasoned from a doc.** A plain
    ``service_ctl.spawn_detached(argv, cwd=...)`` makes the new process a
    genuine Win32 child of THIS process — ``CREATE_NEW_PROCESS_GROUP`` /
    ``CREATE_NO_WINDOW`` change console/signal behaviour, not the recorded
    parent PID. ``update-apply``'s first real action is
    ``service_ctl.stop()``, which on Windows runs
    ``taskkill /PID <backend_pid> /T /F`` — ``/T`` kills the target AND
    EVERY PROCESS WHOSE PARENT CHAIN LEADS BACK TO IT, which includes
    ``update-apply`` itself if it was spawned directly. Confirmed with a
    minimal two-process experiment: a directly-spawned child issuing
    ``taskkill /T`` against its own parent was ALWAYS killed alongside it.
    In production this meant `update-apply` reliably died the instant it
    tried to stop the backend that launched it — `last_apply.started_at`
    set, nothing after it, `current` never repointed, no rollback (nothing
    left alive to attempt one).

    **The fix**: break the direct parent-child link before `update-apply`
    ever calls ``stop()``. ``cmd.exe /c <path to a tiny .bat>`` running
    ``start "" /B <target>`` makes ``cmd.exe`` — not this process — the
    real parent; ``cmd.exe`` exits within milliseconds, so by the time
    `update-apply` calls `stop()`, its recorded parent is already gone and
    a parent-rooted ``taskkill /T`` cannot reach it (there is no live node
    to recurse through). Confirmed with the same experiment: the
    shim-launched child survived the exact same kill that took down its
    logical parent. A ``.bat`` file is used (not an inline ``cmd /c
    "start ... "`` string) because ``subprocess``'s own list-based argv
    quoting on Windows re-quotes a single string element in a way that
    breaks ``cmd``'s notoriously idiosyncratic ``/c`` parsing — writing the
    line to a file sidesteps that class of quoting bug entirely.

    Windows-only, matching the rest of self-update; POSIX's
    ``spawn_detached`` already puts every child in its own new session
    (``start_new_session=True``), so a ``killpg`` targeting the backend's
    process group never reaches a child spawned that way — POSIX had no
    version of this bug to begin with.

    ``cwd`` is threaded through via ``start``'s own ``/D`` switch (not left
    to whatever directory ``cmd.exe`` happens to inherit) for the same
    reason ``service_ctl.target_for_version()`` exists at all: `update-apply`
    is itself invoked as ``-m src.cli update-apply``, and ``-m`` resolution
    depends on the CHILD's cwd, not on `cmd.exe`'s. Getting this wrong would
    reintroduce the cwd bug one level up, for `update-apply`'s own launch
    instead of the switch it performs internally.
    """
    import tempfile

    from . import service_ctl  # noqa: PLC0415

    if os.name != "nt":
        raise NotImplementedError(
            "update-apply spawning is Windows-only for now (self-update's "
            "own scope) -- see the design topic's migration-risk decision"
        )

    tmp_dir = service_ctl.state_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)  # not guaranteed by stage_release's own cleanup
    bat_fd, bat_path = tempfile.mkstemp(
        suffix=".bat", prefix="mnemo-update-apply-", dir=str(tmp_dir)
    )
    quoted = " ".join(f'"{part}"' for part in argv)
    with os.fdopen(bat_fd, "w", encoding="utf-8") as fh:
        fh.write(f'start "" /D "{cwd}" /B {quoted}\r\n')
        # Self-delete as the batch file's own last act, not something this
        # process cleans up afterward: cmd.exe has already read this file
        # into its own buffer by the time it runs a line, so a script
        # deleting itself here is the standard, race-free Windows idiom --
        # unlike an external delete from this process, which would have to
        # guess when cmd.exe is done with it. Without this, every apply
        # (successful or rolled back) left one file behind forever.
        fh.write('del "%~f0"\r\n')
    # cmd.exe itself is still spawned as a direct child of this process --
    # that is fine and expected. It is `update-apply` (cmd's own child,
    # launched via `start /B`) that needs the broken link, and it gets one
    # the moment cmd.exe exits, milliseconds after issuing `start`.
    service_ctl.spawn_detached(["cmd.exe", "/c", bat_path])


def _run_staged_apply(tag: str, *, trigger: str = "manual") -> None:
    """Background body of ``POST /api/update/apply``.

    Runs on its own daemon thread inside the API process — the current
    version keeps answering every OTHER request throughout staging (design
    topic, point 4 of the UX flow: staging never blocks the backend). The
    only outage is the brief stop -> switch -> start `update-apply` performs
    afterwards, and that happens in a SEPARATE detached process — spawned via
    :func:`_spawn_update_apply_breakaway`, not a plain ``spawn_detached``;
    see that function's docstring for why the distinction is load-bearing.

    Deliberately stops updating ``_apply_progress`` once ``update-apply`` is
    spawned, rather than trying to track "switching"/"health" from here:
    the very next thing `update-apply` does is `service_ctl.stop()`, and
    that stops THIS backend process (the one running this thread) — so
    there is no live process left to keep narrating, and no GET request
    could reach it to ask even if there were. See `api_update_status`'s own
    comment for the fuller version of this reasoning: those two states are,
    by construction, never observable from a live HTTP response.

    ``trigger`` ("manual" or "auto") is recorded via
    ``engine_update.set_pending_trigger`` right before ``update-apply`` is
    spawned — that record is how the fact survives the handoff to the
    separately-spawned process, which shares no memory with this thread and
    needs to know whether to attribute its own outcome to the auto-apply
    blacklist (``engine_update.record_auto_outcome``).
    """
    from . import engine_update, service_ctl

    def _on_progress(payload: dict) -> None:
        if payload.get("tag") != tag:
            return
        _touch_apply_progress(
            step=payload.get("step"),
            detail=payload.get("detail"),
            error=payload.get("error") if payload.get("step") == "failed" else _apply_progress["error"],
        )

    _touch_apply_progress(
        state="staging", tag=tag, step="download", error=None,
        started_at=_now_iso(), finished_at=None, trigger=trigger,
    )
    engine_update.add_progress_listener(_on_progress)
    try:
        engine_update.stage_release(tag)
    except Exception as exc:  # noqa: BLE001 - reported via _apply_progress, not raised into a void
        _touch_apply_progress(
            state="failed", step="failed", error=str(exc), finished_at=_now_iso()
        )
        return
    finally:
        engine_update.remove_progress_listener(_on_progress)

    _touch_apply_progress(state="switching", step=None, finished_at=None)
    engine_root = Path(__file__).resolve().parent.parent
    cmd = [service_ctl.windowless_python(), "-m", "src.cli", "update-apply"]
    engine_update.set_pending_trigger(tag, trigger)
    try:
        _spawn_update_apply_breakaway(cmd, engine_root)
    except OSError as exc:
        # Staging succeeded but we could not even START update-apply (a
        # broken interpreter, a permissions error) -- the running service is
        # completely untouched at this point (stage_release never touches
        # `current`), so this is a clean failure, not a half-applied one.
        _touch_apply_progress(
            state="failed", step="failed",
            error=f"could not spawn update-apply: {exc}", finished_at=_now_iso(),
        )


def _apply_view(state: dict) -> dict:
    """Merge this process's own (in-flight or just-finished) staging state
    with what ``engine_version.json`` durably records.

    ``_apply_progress`` reflects only a staging attempt STARTED BY THIS
    PROCESS, and it never resets itself. A naive "prefer disk only while we
    are idle" rule would make one failed staging attempt repeat that same
    verdict forever afterwards — masking a genuinely newer ``last_apply``
    written by a completely different `update-apply` run (by hand, from a
    different process, or a retry after this process's own attempt failed).

    Comparing the two sides' ``started_at`` VALUES does not work either: a
    stuck apply is, by definition, one whose ``started_at`` is old — using
    it as a recency signal would make a genuinely stuck record lose to a
    fresher-looking (but actually stale) in-memory state. What actually
    answers "which side changed most recently" is **when each side was last
    WRITTEN**, not what either one's `started_at` field says happened. So
    this compares the state file's own mtime (when anything last wrote a
    NEW `engine_version.json` — a real switch-time signal `update-apply`,
    hand edits, or a different process all produce) against
    ``_apply_progress_touched_at`` (when THIS process's in-memory tracker
    last changed) — whichever happened more recently in wall-clock time
    wins.
    """
    from . import engine_update  # noqa: PLC0415

    last_apply = state.get("last_apply") or {}
    disk_tag = last_apply.get("tag")
    disk_mtime = 0.0
    with suppress(OSError):
        disk_mtime = engine_update.version_state_file().stat().st_mtime

    if disk_tag and disk_mtime >= _apply_progress_touched_at:
        if last_apply.get("finished_at"):
            result_to_state = {
                "applied": "done", "rolled_back": "rolled_back", "failed": "failed",
            }
            return {
                "state": result_to_state.get(last_apply.get("result"), "failed"),
                "tag": disk_tag, "step": None, "detail": None, "error": last_apply.get("error"),
                "started_at": last_apply.get("started_at"),
                "finished_at": last_apply.get("finished_at"),
                "trigger": last_apply.get("trigger"),
            }
        return {
            "state": "switching", "tag": disk_tag, "step": None, "detail": None, "error": None,
            "started_at": last_apply.get("started_at"), "finished_at": None,
            "trigger": last_apply.get("trigger"),
        }
    return dict(_apply_progress)


@app.get("/api/update/status", include_in_schema=False)
def api_update_status() -> dict:
    """Read-only aggregate of ``engine_version.json`` plus this process's
    own in-flight staging state, if any (merged by :func:`_apply_view`).

    ``apply.state`` for "started but not finished, and we are not the one
    staging it right now" is reported as ``"switching"`` — the closest
    honest single label available. It genuinely cannot distinguish
    `update-apply`'s "switching" phase from its "health" phase: that
    process (a) is not this one and (b) writes no intermediate step to
    disk, only a start and a terminal finish. This is not a gap that
    matters in practice: the OLD backend that could serve THIS endpoint is
    the one `update-apply` stops to perform both phases, so no live HTTP
    response can ever originate from inside them anyway — by the time
    something answers `/api/update/status` again, the switch has already
    either succeeded (a new process, serving the new tag) or failed and
    rolled back (a new process, serving the old tag again) or died
    entirely (nothing answers; that is `mnemo doctor`'s job, step 10).
    """
    from . import engine_update

    state = engine_update.read_state()
    current_tag = engine_update.effective_current_tag(state)
    current_entry = next(
        (e for e in state.get("installed", []) if e.get("tag") == current_tag), None
    )
    last_check = state.get("last_check") or {}
    apply_view = _apply_view(state)

    return {
        "current": {
            "tag": current_tag,
            "installed_at": (current_entry or {}).get("installed_at"),
            "commit": (current_entry or {}).get("commit"),
        },
        "latest_known": {
            "tag": last_check.get("latest_tag"),
            "checked_at": last_check.get("at"),
            "update_available": bool(last_check.get("update_available")),
        },
        "check": {
            "in_progress": engine_update.check_in_progress(),
            "error": last_check.get("error"),
        },
        "apply": apply_view,
        "history": state.get("installed", []),
        "retention": {"keep": config.UPDATE_RETENTION_COUNT},
        "auto": _auto_status_view(state),
    }


def _auto_status_view(state: dict) -> dict:
    """The ``"auto"`` block of ``GET /api/update/status``: whether
    unattended auto-apply is on, what (if anything) is currently counting
    down, and the per-tag blacklist -- everything needed to explain why
    auto-apply is silently skipping a tag, without the client having to
    guess.

    ``seconds_left`` is computed HERE, server-side, at response time
    (``max(0, deadline - now)``) rather than trusted from a client-side
    timer -- the exact "status-poll is the truth, WS is only a live
    preview" discipline this feature already established for the apply
    progress modal (see ``_apply_view``'s own docstring), so a page reload
    mid-countdown resumes showing the correct remaining time.
    """
    with _auto_pending_lock:
        pending = dict(_auto_pending) if _auto_pending is not None else None

    if pending is not None:
        try:
            deadline_dt = datetime.fromisoformat(pending["deadline"])
            seconds_left = max(0, int((deadline_dt - datetime.now(timezone.utc).astimezone()).total_seconds()))
        except ValueError:
            seconds_left = 0
        pending = {**pending, "seconds_left": seconds_left}

    auto = state.get("auto") or {}
    blacklist = auto.get("blacklist") or {}
    return {
        "enabled": settings.auto_update_enabled(),
        "pending": pending,
        "blacklist": [
            {
                "tag": tag,
                "attempts": entry.get("attempts", 0),
                "blacklisted": bool(entry.get("blacklisted")),
                "last_error": entry.get("last_error"),
                "last_failed_at": entry.get("last_failed_at"),
                "next_retry_at": entry.get("next_retry_at"),
            }
            for tag, entry in blacklist.items()
        ],
    }


@app.post("/api/update/check", include_in_schema=False)
def api_update_check() -> dict:
    """Synchronous ``check_latest_release()`` + ``record_check()`` (step 6,
    unchanged) — one real GitHub round trip, budgeted by
    ``config.UPDATE_CHECK_TIMEOUT_S``.
    """
    from . import engine_update

    state = engine_update.check_now()
    last_check = state.get("last_check") or {}
    return {
        "latest_tag": last_check.get("latest_tag"),
        "current_tag": engine_update.effective_current_tag(state),
        "update_available": bool(last_check.get("update_available")),
        "checked_at": last_check.get("at"),
        "error": last_check.get("error"),
    }


def _begin_apply(tag: str, *, trigger: str) -> None:
    """Guard-and-start: the shared body of both the manual apply endpoint
    and the auto-apply countdown's settle path.

    Same ``stale_target``/``update_in_progress`` guards either way -- an
    auto-triggered apply cannot start against a tag that has gone stale, or
    stack on top of one already running, any more than a manual one can.
    Extracted from ``api_update_apply`` unchanged (pure refactor): the
    manual endpoint below is now a thin wrapper over this with
    ``trigger="manual"``, and its behaviour/response shape is unchanged.
    """
    from . import engine_update

    state = engine_update.read_state()
    last_check = state.get("last_check") or {}
    if tag != last_check.get("latest_tag") or not last_check.get("update_available"):
        raise ApiError(
            "stale_target",
            f"{tag!r} does not match the last known latest tag "
            f"({last_check.get('latest_tag')!r}) — run a check again",
            tag=tag, latest_tag=last_check.get("latest_tag"),
        )
    if _apply_progress["state"] not in ("idle", "done", "failed", "rolled_back"):
        raise ApiError(
            "update_in_progress",
            f"an update is already {_apply_progress['state']} "
            f"(tag {_apply_progress.get('tag')!r})",
        )

    thread = threading.Thread(
        target=_run_staged_apply, args=(tag,), kwargs={"trigger": trigger},
        name=f"mnemo-update-apply-{tag}", daemon=True,
    )
    thread.start()


@app.post("/api/update/apply", status_code=202, include_in_schema=False)
def api_update_apply(req: UpdateApplyRequest) -> dict:
    """Stage ``req.tag`` and, on success, hand off to the detached
    `update-apply` CLI (step 8). Returns immediately; the real work happens
    on a background thread started here.
    """
    _begin_apply(req.tag, trigger="manual")
    return {"accepted": True, "tag": req.tag}


@app.post("/api/update/auto/confirm", status_code=202, include_in_schema=False)
def api_update_auto_confirm() -> dict:
    """Confirm the pending auto-apply countdown right now, instead of
    waiting out the remaining seconds.

    Still an "auto" trigger for blacklist purposes on failure -- clicking OK
    during the countdown is not a manual apply, it is skipping ahead on an
    apply that was already going to happen automatically (resolved flag,
    self-update auto-apply design note).
    """
    with _auto_pending_lock:
        pending = dict(_auto_pending) if _auto_pending is not None else None
    if pending is None:
        raise ApiError("auto_not_pending", "no auto-apply countdown is pending right now")
    _settle_auto_pending(pending["tag"])
    return {"accepted": True, "tag": pending["tag"]}


@app.post("/api/update/auto/cancel", include_in_schema=False)
def api_update_auto_cancel() -> dict:
    """Cancel the pending auto-apply countdown.

    Touches nothing durable: no blacklist write, no state persisted beyond
    clearing the in-memory pending dict (resolved flag). The same tag's
    countdown reappears on the next checker tick (~``UPDATE_CHECK_INTERVAL_S``
    later) -- accepted as-is, no snooze/cooldown period for now.
    """
    global _auto_pending, _auto_pending_timer
    with _auto_pending_lock:
        pending = dict(_auto_pending) if _auto_pending is not None else None
        timer = _auto_pending_timer
        if pending is not None:
            _auto_pending = None
            _auto_pending_timer = None
    if pending is None:
        raise ApiError("auto_not_pending", "no auto-apply countdown is pending right now")
    if timer is not None:
        timer.cancel()
    hub.publish("update_auto_pending", {"phase": "cancelled", "tag": pending["tag"]}, None)
    return {"accepted": True, "tag": pending["tag"]}


@app.get("/api/logs", include_in_schema=False)
# NOTE for internal callers (the admin MCP tools call this directly, as they
# must): `limit` and `offset` default to `Query(...)` descriptors, not to
# numbers. FastAPI substitutes a real value per request; a plain Python call
# does not, and the descriptor travels into `servicelog` and blows up there.
# Pass both explicitly.
def api_logs(
    kind: Literal["query", "index"],
    bank: str | None = None,
    since: str | None = None,
    until: str | None = None,
    event_kind: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    bank_id = _resolve_bank(bank, require_enabled=False).id if bank else None
    t_since, t_until = _parse_time(since), _parse_time(until)
    if kind == "query":
        events = servicelog.read_queries(
            bank_id=bank_id, since=t_since, until=t_until,
            limit=limit, offset=offset,
        )
        total = servicelog.count(
            "query", bank_id=bank_id, since=t_since, until=t_until
        )
    else:
        events = servicelog.read_index(
            bank_id=bank_id, since=t_since, until=t_until, kind=event_kind,
            limit=limit, offset=offset,
        )
        total = servicelog.count(
            "index", bank_id=bank_id, since=t_since, until=t_until,
            kind=event_kind,
        )
    return {
        "kind": kind,
        "total": total,
        "limit": limit,
        "offset": offset,
        "events": events,
    }


# -------------------------------------------------------------- websocket


@app.websocket("/ws")
async def ws_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    bank: str | None = Query(default=None),
) -> None:
    """Live service events. The token rides in the query string because a
    browser cannot set headers on a WebSocket handshake (§9.1).

    Same gate as the rest of `/api` (2026-08-21, refined 2026-08-25 by
    MN-19): with nothing gating `/api` right now, this connects with no
    token presented. `_token_ok(token)` alone would reject an empty `token`
    outright regardless of configuration — it has no notion of "auth is off
    right now" — so that check only runs once `_api_gated()` says a token is
    actually required. `_token_ok` mints via `api_token()` internally, so no
    separate mint call is needed here.
    """
    if _api_gated() and not _token_ok(token):
        await websocket.close(code=1008)
        return
    await hub.connect(websocket, bank)
    try:
        # Reconnect contract: `hello` means "refetch everything over REST".
        # The socket carries deltas only and REST is authoritative for
        # initial state, so a client that missed events while disconnected
        # heals by re-reading — no sequence numbers, no server-side replay,
        # and no way for a gap to persist unnoticed.
        await hub.send(
            websocket,
            hub.envelope(
                "hello",
                {
                    "version": SERVICE_VERSION,
                    "refetch": True,
                    "banks": [b.id for b in registry.load()],
                    "queue": _queue_snapshot_json(),
                },
                None,
            ),
        )
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue  # clients must tolerate us ignoring noise, and vice versa
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "subscribe":
                hub.subscribe(websocket, msg.get("bank_id"))
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(websocket)


# ------------------------------------------------------ agent chat websocket
# (MN-43)


@app.websocket("/ws/agents/{slug}/chats/{chat_id}")
async def ws_agent_chat(
    websocket: WebSocket,
    slug: str,
    chat_id: str,
    token: str | None = Query(default=None),
) -> None:
    """Live byte-mirror of one chat's real ``claude`` PTY session.

    Same gate as `/ws` above — same reasoning (`_api_gated()`), same
    query-string token (a browser cannot set headers on a WS handshake).
    Envelopes (§43.3): server -> client is one of ``output`` (a chunk of PTY
    output), ``replay_done`` (history replay finished, live output follows),
    ``exited`` (the process terminated), ``error``; client -> server is
    ``input`` (keystrokes) or ``resize`` (terminal dimensions).

    Connecting spawns the real process on the FIRST subscriber for this
    ``chat_id`` (`agent_runtime.ensure_and_subscribe`) — cheap for every
    connection after that, since the process is already running and this
    call only subscribes. Any number of simultaneous viewers of the same
    chat see the same live stream; closing this socket never stops the
    process, only removes this one subscriber.
    """
    if _api_gated() and not _token_ok(token):
        await websocket.close(code=1008)
        return
    await websocket.accept()

    try:
        sub_id, queue, replay_text = agent_runtime.ensure_and_subscribe(slug, chat_id)
    except agent_registry.AgentNotFound as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1008)
        return
    except agent_registry.ChatNotFound as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1008)
        return
    except agent_runtime.SessionLimitExceeded as exc:
        await websocket.send_json(
            {"type": "error", "code": "too_many_sessions", "message": str(exc)}
        )
        await websocket.close(code=1013)  # "try again later" (RFC 6455 IANA registry)
        return
    except agent_runtime.ClaudeNotFound as exc:
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)
        return
    except Exception as exc:  # noqa: BLE001 - a spawn failure is not our bug to hide
        log.exception("failed to spawn/subscribe chat %s for agent %s", chat_id, slug)
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)
        return

    if replay_text:
        await websocket.send_json({"type": "output", "data": replay_text})
    await websocket.send_json({"type": "replay_done"})

    async def pump_queue() -> None:
        while True:
            item = await queue.get()
            try:
                await websocket.send_json(item)
            except Exception:  # noqa: BLE001 - a dead socket ends the pump, not the process
                break
            if item.get("type") == "exited":
                break

    pump_task = asyncio.create_task(pump_queue())
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue  # same tolerance contract as `/ws` above
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "input":
                agent_runtime.send_input(chat_id, str(msg.get("data", "")))
            elif kind == "resize":
                with suppress(Exception):
                    agent_runtime.resize(
                        chat_id, int(msg.get("rows", 24)), int(msg.get("cols", 80))
                    )
    except WebSocketDisconnect:
        pass
    finally:
        pump_task.cancel()
        with suppress(asyncio.CancelledError):
            await pump_task
        agent_runtime.unsubscribe(chat_id, sub_id)


# The console's assets — `src.webui.STATIC_DIR` and nothing else from that
# package. Mounting the package directory would put `devserver.py` (a
# developer tool) on the request path; the static tree is the only thing the
# service serves. Mounted only when ui-dev has shipped it, so the backend
# runs headless until then.
try:
    from .webui import STATIC_DIR as _STATIC_DIR
except ImportError:
    _STATIC_DIR = None
if _STATIC_DIR is not None and _STATIC_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=_STATIC_DIR, html=True), name="ui")

# ------------------------------------------------ /mcp-tools — tool mirror
#
# The same three tools as plain HTTP: send a request, get an answer, no
# JSON-RPC and no streaming (§9.8). This is the surface for a person with curl
# and for a script asking "does it answer at all" — agents use `/mcp`.
#
# It is a MIRROR, not a second API, and three things keep it honest: the paths
# are the tool names verbatim, the parameters and defaults are copied from the
# tool signatures, and the body is produced by the very same `run_*` functions
# the tools call. `?format=json` only wraps that same string — it does not
# restructure it, because two shapes would mean two contracts to keep in step.
#
# **It keeps `bank`, which the plain face's tools no longer have.** That is not
# drift: `search`/`tree` on `/mcp/<bank>` are addressed by URL and may open
# with that bank's own token, so a `bank` argument there would let one bank's
# credential read another. This surface takes the SERVICE token, which already
# opens every bank, so naming one per call adds no reach and is the only way
# to hand-test across banks from Swagger. `reindex` mirrors the admin face's
# tool (it left the plain face with phase 4's split) and carries its `full`.
#
# These are also the ONLY routes in the OpenAPI schema: every `/api` route is
# `include_in_schema=False`, so `/docs` shows what is meant to be looked at
# from outside and not the console's private plumbing.

_MIRROR_TAGS = ["mcp-tools"]


def _mirror(tool: str, text: str, fmt: str, bank: str | None):
    """text/plain by default — byte for byte what the agent reads."""
    if fmt == "json":
        return {"tool": tool, "bank": bank, "text": text}
    return PlainTextResponse(text)


@app.get("/mcp-tools/search", tags=_MIRROR_TAGS,
         dependencies=[Security(bearer_scheme)])
def mcp_tools_search(
    query: str,
    top_k: int = 5,
    path_prefix: str | None = None,
    bank: str | None = None,
    format: Literal["text", "json"] = "text",
):
    """Search this project's curated memory. Returns numbered sections."""
    from .mcp_server import run_search

    # face="mcp-tools": a hand-poked query must not be counted as an agent's
    # in the journal, or the usage numbers stop meaning anything.
    text = run_search(query, top_k, path_prefix, bank, face="mcp-tools")
    return _mirror("search", text, format, bank)


@app.get("/mcp-tools/tree", tags=_MIRROR_TAGS,
         dependencies=[Security(bearer_scheme)])
def mcp_tools_tree(
    path_prefix: str | None = None,
    depth: int = 3,
    bank: str | None = None,
    format: Literal["text", "json"] = "text",
):
    """Show the memory tree, with each file's headings."""
    from .mcp_server import run_tree

    return _mirror("tree", run_tree(path_prefix, depth, bank),
                   format, bank)


@app.post("/mcp-tools/reindex", tags=_MIRROR_TAGS,
          dependencies=[Security(bearer_scheme)])
def mcp_tools_reindex(
    path: str | None = None,
    bank: str | None = None,
    full: bool = False,
    format: Literal["text", "json"] = "text",
):
    """Queue a reindex of one file, or of the whole bank."""
    from .mcp_server import run_reindex

    # POST, unlike its two siblings: this one changes state (it queues work).
    # The others stay GET so they can be pasted into an address bar.
    return _mirror("reindex", run_reindex(path, bank, full), format, bank)


# MCP over HTTP, in this same process and this same uvicorn — nothing is
# spawned for a Claude Code session (NFR-2). Mounted last so the /api routes
# above are matched first. The bank travels as the path segment after /mcp.
#
# `/mcp-admin` is mounted BEFORE `/mcp` and is a separate path, not a bank:
# the bank-routing shim under `/mcp` would otherwise read `admin` as a bank
# name. Starlette matches mounts in declaration order, so the more specific
# prefix has to be declared first.
try:
    from .mcp_admin import build_app as _build_admin
    from .mcp_server import build_app as _build_mcp
except ImportError:  # pragma: no cover - the SDK is a hard dep, but be honest
    _build_admin = _build_mcp = None
if _build_admin is not None:
    app.mount("/mcp-admin", _build_admin(), name="mcp-admin")
if _build_mcp is not None:
    app.mount("/mcp", _build_mcp(), name="mcp")


def run(host: str | None = None, port: int | None = None) -> None:
    """Serve. ``mnemo service start`` (phase 5) calls this in a child.

    ``uvicorn.Config`` + ``uvicorn.Server`` instead of the ``uvicorn.run()``
    shortcut only so the server instance can be reached from a request
    handler (``app.state.uvicorn_server``, read by ``POST /api/shutdown`` —
    MN-11). Behaviourally identical to ``uvicorn.run()`` otherwise: same
    host/port/log-level, same call to ``server.run()``.
    """
    import uvicorn

    config = uvicorn.Config(
        app,
        host=host or API_HOST,
        port=port or API_PORT,
        log_level=os.environ.get("MNEMO_LOG_LEVEL", "info"),
    )
    server = uvicorn.Server(config)
    app.state.uvicorn_server = server
    server.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
