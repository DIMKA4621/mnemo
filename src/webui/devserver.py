"""DEV-ONLY mock backend for the web console. Never part of the request path.

Phase 6 is built ahead of phases 2-4 on purpose, so this script stands in for
`src/api.py` until it exists: it serves `static/` at `/ui` and answers the
subset of contract 9.5 / 9.7 that the console actually calls, using the JSON
fixtures in `fixtures/`. Every response is shaped exactly like the contract —
if it ever diverges, the fixture is the bug, not the page.

Nothing here is imported by the real service. `src/api.py` (service-dev) mounts
only `src.webui.STATIC_DIR`; this module is a developer tool.

    python src/webui/devserver.py            # http://127.0.0.1:8919/ui/?token=...
    python src/webui/devserver.py --port 9000 --noise 8

Stdlib only, including the WebSocket framing — a mock must not add a
dependency the product does not have.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import secrets
import socket
import struct
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
FIXTURES = HERE / "fixtures"

# A real token is 48 hex chars (contract 9.1); this one is fixed so the dev URL
# is stable across restarts.
DEV_TOKEN = "de7a" * 12

# Set by --no-auth: the real backend is open by default (contract 9.1, unless
# $MNEMO_API_TOKEN/require_login is set) — this mock hardcoded a token
# unconditionally instead, which doesn't match that default. Off by default
# so existing dev URLs/scripts built around DEV_TOKEN keep working.
NO_AUTH = False

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SERVICE_VERSION = "3.0.0-mock"

STARTED_AT = time.time()

# --------------------------------------------------------------------------
# fixture state (mutable — reindex actually moves it)
# --------------------------------------------------------------------------

_lock = threading.RLock()


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


BANKS: list[dict] = _load("banks.json")["banks"]
TREES: dict[str, dict] = _load("trees.json")
FILES: dict[str, dict] = _load("files.json")
_logs = _load("logs.json")
QUERY_EVENTS: list[dict] = _logs["query"]
INDEX_EVENTS: list[dict] = _logs["index"]

_next_index_id = max((e["id"] for e in INDEX_EVENTS), default=0) + 1
_next_query_id = max((e["id"] for e in QUERY_EVENTS), default=0) + 1
_task_seq = 0

QUEUE: dict[str, Any] = {"depth": 0, "high": 0, "normal": 0, "low": 0, "current": None}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def now_iso_ms() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def find_bank(ref: str) -> dict | None:
    """Contract 6.4: id, then name, then path (exact root or deepest ancestor)."""
    ref = (ref or "").strip()
    if not ref:
        return None
    with _lock:
        for bank in BANKS:
            if bank["id"] == ref:
                return bank
        for bank in BANKS:
            if bank["name"].strip().lower() == ref.lower():
                return bank
        posix = ref.replace("\\", "/").rstrip("/").lower()
        best = None
        for bank in BANKS:
            root = bank["root"].rstrip("/").lower()
            if posix == root or posix.startswith(root + "/"):
                if best is None or len(root) > len(best["root"]):
                    best = bank
        return best


def _project_root_for_bank(bank: dict) -> str | None:
    """`<project>/.claude/memory` -> `<project>`, else None.

    Fixture-only mirror of `_project_root_from_bank` in api.py (MN-13,
    contract: GET /api/banks/{id}/mcp-wiring) — same suffix rule the console's
    own `projectRootForBankPath()` in app.js applies before it ever calls
    this endpoint, so a bank that fails it here never gets asked twice.
    """
    root = (bank.get("root") or "").replace("\\", "/").rstrip("/")
    suffix = "/.claude/memory"
    if not root.endswith(suffix):
        return None
    return root[: -len(suffix)] or None


# Fixture-only: which fixture banks have MCP wiring in their project root.
# The real endpoint checks the filesystem; this mock has none to check, so
# it just answers per bank id. Absent from this map == no wiring found.
_MCP_WIRING: dict[str, dict] = {
    "42c3326ed6e1a1b7": {"has_wiring": True, "uses_template": False},
}


# --------------------------------------------------------------------------
# fixture filesystem, for the bank picker (contract 9.5 GET /api/fs/dirs)
# --------------------------------------------------------------------------
#
# Invented, not read off the disk. Browsing the real filesystem here would let
# a UI mistake register a real folder against a fake service — and the point of
# this server is to answer *shapes*, not to be the backend.

FS_HOME = "/home/dev"

FS_FIXTURE: dict[str, list[str]] = {
    "/": ["home", "srv"],
    "/home": ["dev"],
    "/home/dev": [".claude", "notes", "projects", "empty-folder"],
    "/home/dev/.claude": ["memory", "skills"],
    "/home/dev/.claude/memory": ["logs"],
    "/home/dev/.claude/memory/logs": [],
    "/home/dev/.claude/skills": [],
    "/home/dev/notes": [],
    "/home/dev/projects": ["mnemo", "voice-agent", "legacy-tool"],
    "/home/dev/projects/mnemo": ["docs"],
    "/home/dev/projects/mnemo/docs": [],
    # Agents wizard's "Вказати наявну теку" adoption-preview demo — see
    # `_AGENT_ADOPTABLE` below (MN-42 Фаза B).
    "/home/dev/projects/legacy-tool": [],
    # `.claude/memory` here specifically, so the add-bank picker's "одразу
    # підключити проєкт (MCP)" checkbox has a second eligible target — one
    # whose fixture `init` outcome (`_mock_init_result`) is the *honest
    # refusal* shape (`ok: true`, log carries a git-rm-cached message),
    # distinct from `/home/dev/.claude/memory`'s plain success.
    "/home/dev/projects/voice-agent": [".claude"],
    "/home/dev/projects/voice-agent/.claude": ["memory"],
    "/home/dev/projects/voice-agent/.claude/memory": [],
    "/home/dev/empty-folder": [],
    "/srv": [],
}

# `.md` under each path, counted the way the real endpoint counts: recursively.
FS_MD: dict[str, int] = {
    "/home/dev": 74,
    "/home/dev/.claude": 61,
    "/home/dev/.claude/memory": 61,
    "/home/dev/.claude/memory/logs": 24,
    "/home/dev/notes": 13,
    "/home/dev/projects": 0,
    "/home/dev/projects/mnemo": 9,
    "/home/dev/projects/mnemo/docs": 9,
    "/home/dev/projects/legacy-tool": 5,
    "/home/dev/projects/voice-agent": 5,
    "/home/dev/projects/voice-agent/.claude": 5,
    "/home/dev/projects/voice-agent/.claude/memory": 5,
}


# --------------------------------------------------------------------------
# fixture machine settings (contract 9.5 GET/PUT /api/settings, §6.6)
# --------------------------------------------------------------------------
#
# Held in memory and genuinely mutated by PUT, so the dev page exercises the
# save path rather than a stub that always answers the same thing. Never read
# from — and never written to — the machine's real `state/settings.json`: a
# dev mock that edited it could switch this machine's provider from a page
# that is still being written.
#
# The catalogue is imported from `presets`, not copied. It is pure data with
# no service dependency, and duplicating it here is exactly how a fixture ends
# up disagreeing with the product it stands in for.

_SETTINGS: dict[str, Any] = {
    "provider": "local",
    "auto_update": True,
    "api": {"url": "", "model": "", "dim": 0, "timeout": 60.0, "key": ""},
}

# One value pinned to "env" so the override path — the warning that a saved
# value cannot take effect — is reachable in dev. It is the timeout precisely
# because it is the least consequential: nothing else in the form is blocked.
_SETTINGS_ENV = {"api.timeout": "MNEMO_API_EMBED_TIMEOUT"}

# Autostart, faked. Registering a real logon task from a dev fixture would
# reach past the browser and change the machine — the one thing a mock server
# must never do. It flips in memory, which is all the checkbox needs to be
# exercised.
_AUTOSTART: dict[str, Any] = {
    "supported": True,
    "enabled": True,
    "mechanism": "Task Scheduler (фікстура)",
    "name": "mnemo service",
}

# Backend memory, faked for the same reason as autostart: unloading for real
# would stop this machine's actual resident from a dev fixture. It flips in
# memory, which is all the two buttons need. Starts `loaded`, because that is
# the state where both of them are reachable.
_EMBED_HELD: dict[str, Any] = {"holding": "loaded", "probe_dim": None}
# `cached=False` by default so the freshly-added "Завантажити модель" button
# (contract: `GET /api/embed/state`'s `download` field) is on screen without
# extra clicks — the whole point of this fixture is to exercise that branch.
_MODEL_CACHED: dict[str, Any] = {"value": False}
_DOWNLOAD: dict[str, Any] = {"active": False, "failed": False}

# Disposable fixture indexes only — never paths in the real state directory.
# Two shapes keep the maintenance UI honest: one whose source root still
# exists (deleting it is a meaningful choice), and one pre-v3 index with no
# recorded root at all.
_ORPHANS: list[dict[str, Any]] = [
    {
        "id": "deadbeefdeadbeef",
        "path": "/home/dev/.mnemo/state/deadbeefdeadbeef.db",
        "size": 7340032,
        "root": "/home/dev/old-project/.claude/memory",
        "root_exists": True,
        "schema": "3",
        "files": 18,
        "last_indexed": "2026-07-20T18:14:00+03:00",
        "error": None,
    },
    {
        "id": "facefeedfacefeed",
        "path": "/home/dev/.mnemo/state/facefeedfacefeed.db",
        "size": 1048576,
        "root": None,
        "root_exists": False,
        "schema": None,
        "files": 4,
        "last_indexed": None,
        "error": None,
    },
]

# The general MCP/Skills/Rules registry (MN-41/MN-42, `src/catalog.py`), for
# the Реєстр page. Shaped exactly like `_entry_info()`'s response — `vars` is
# stored pre-computed here rather than derived per request, since this is a
# fixture and there is no `catalog.py` behind it to call.
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def _catalog_vars(content: str) -> list[str]:
    seen: dict[str, None] = {}
    for m in _VAR_RE.finditer(content or ""):
        seen.setdefault(m.group(1), None)
    return list(seen)


def _canonical_json(content: str) -> str | None:
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError, RecursionError):
        return None
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False)


CATALOG: list[dict[str, Any]] = [
    {
        "id": "mcp_a1b2c3d4e5f6",
        "category": "mcp",
        "name": "mnemo-memory",
        "content": '{\n  "type": "http",\n  "url": "http://127.0.0.1:4646/mcp"\n}',
        "created_at": "2026-08-10T09:00:00+03:00",
    },
    {
        "id": "mcp_b2c3d4e5f6a1",
        "category": "mcp",
        "name": "github",
        "content": '{\n  "type": "stdio",\n  "command": "gh",\n  "args": ["mcp", "serve"]\n}',
        "created_at": "2026-08-11T09:00:00+03:00",
    },
    {
        "id": "mcp_c3d4e5f6a1b2",
        "category": "mcp",
        "name": "atlassian",
        "content": '{\n  "type": "http",\n  "url": "https://mcp.atlassian.com/v1/sse",\n'
        '  "headers": { "Authorization": "Bearer {{ATLASSIAN_API_TOKEN}}" }\n}',
        "created_at": "2026-08-12T09:00:00+03:00",
    },
    {
        "id": "skill_d4e5f6a1b2c3",
        "category": "skill",
        "name": "keyboard-layout",
        "content": "Конвертує текст, набраний не в тій розкладці, між EN (QWERTY) і UA (ЙЦУКЕН). "
        "Спрацьовує на «гібришний» текст.",
        "created_at": "2026-08-13T09:00:00+03:00",
    },
    {
        "id": "skill_e5f6a1b2c3d4",
        "category": "skill",
        "name": "code-review",
        "content": "Ревʼю поточного диму на баги та можливості спрощення/перевикористання коду, "
        "з рівнями зусиль low/medium/high.",
        "created_at": "2026-08-14T09:00:00+03:00",
    },
    {
        "id": "rule_f6a1b2c3d4e5",
        "category": "rule",
        "name": "v3-build.md",
        "content": "Джерело істини для v3: чотири доки Memory-{design,requirements,implementation,"
        "contracts}-v3.md. Ніколи не комітити/пушити самостійно, ніякого Co-Authored-By.",
        "created_at": "2026-08-15T09:00:00+03:00",
    },
    {
        "id": "rule_a1b2c3d4e5f7",
        "category": "rule",
        "name": "git-workflow.md",
        "content": "Гілки, PR і релізи: нова робота — завжди нова гілка від актуального master; "
        "реліз — завжди чернетка.",
        "created_at": "2026-08-16T09:00:00+03:00",
    },
]


def _catalog_entry_info(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "category": entry["category"],
        "name": entry["name"],
        "content": entry["content"],
        "created_at": entry["created_at"],
        "vars": _catalog_vars(entry["content"]) if entry["category"] == "mcp" else [],
        # No agent/links fixture exists yet (Phase B, not landed) — static 0
        # until one does, mirroring the real `used_by_count` shape.
        "used_by_count": 0,
    }


def _find_catalog_entry(entry_id: str) -> dict[str, Any] | None:
    return next((e for e in CATALOG if e["id"] == entry_id), None)


def _norm_catalog_name(name: str) -> str:
    return name.strip().casefold()


def _unique_catalog_name(candidate: str, category: str, *, exclude_id: str | None = None) -> str:
    taken = {
        _norm_catalog_name(e["name"])
        for e in CATALOG
        if e["category"] == category and (exclude_id is None or e["id"] != exclude_id)
    }
    base = candidate.strip() or category
    if _norm_catalog_name(base) not in taken:
        return base
    n = 2
    while _norm_catalog_name(f"{base}-{n}") in taken:
        n += 1
    return f"{base}-{n}"


def _find_duplicate_mcp(content: str, *, exclude_id: str | None) -> dict[str, Any] | None:
    normalized = _canonical_json(content)
    if normalized is None:
        return None
    for entry in CATALOG:
        if entry["category"] != "mcp" or entry["id"] == exclude_id:
            continue
        if _canonical_json(entry["content"]) == normalized:
            return entry
    return None


# --------------------------------------------------------------------------
# agent registry fixture (MN-40 backend / MN-42 Фаза B frontend)
# --------------------------------------------------------------------------
#
# Shaped exactly like `_agent_info()`'s response (`src/api.py`'s agent
# registry section) — `launch` is the same `read_launch_config()` shape
# (`{"mode": "standard"}` or `{"mode": "custom", "host", "port", ...}`).
# `AGENTS[].chats` does not exist: MN-43 (the chat backend) isn't built yet,
# so the tree's chat lists are always empty — nothing here needs to fake one.

_AGENT_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _agent_slugify(name: str) -> str:
    base = _AGENT_SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return base or "agent"


def _unique_agent_slug(candidate: str) -> str:
    with _lock:
        taken = {a["slug"] for a in AGENTS}
    if candidate not in taken:
        return candidate
    n = 2
    while f"{candidate}-{n}" in taken:
        n += 1
    return f"{candidate}-{n}"


AGENTS: list[dict[str, Any]] = [
    {
        "slug": "debug-assistant",
        "name": "Дебаг-асистент проєкту",
        "root": "/home/dev/.mnemo/agents/debug-assistant",
        "owns_root": True,
        "created_at": "2026-08-20T09:00:00+03:00",
        "bank_id": "agent_debug_assistant",
        "bank_name": "Дебаг-асистент проєкту",
        "launch": {"mode": "standard"},
    },
    {
        "slug": "release-manager",
        "name": "Реліз-менеджер",
        "root": "/home/dev/.mnemo/agents/release-manager",
        "owns_root": True,
        "created_at": "2026-08-21T09:00:00+03:00",
        "bank_id": "agent_release_manager",
        "bank_name": "Реліз-менеджер",
        "launch": {"mode": "custom", "host": "127.0.0.1", "port": 8790, "model": "claude-sonnet-5"},
    },
    {
        "slug": "ui-tester",
        "name": "Тестувальник UI",
        "root": "/home/dev/.mnemo/agents/ui-tester",
        "owns_root": True,
        "created_at": "2026-08-22T09:00:00+03:00",
        "bank_id": "agent_ui_tester",
        "bank_name": "Тестувальник UI",
        "launch": {"mode": "standard"},
    },
]


def _find_agent(slug: str) -> dict[str, Any] | None:
    with _lock:
        return next((a for a in AGENTS if a["slug"] == slug), None)


# Adoption-preview demo: one path in `FS_FIXTURE` that already looks like an
# agent workspace, so the wizard's "Вказати наявну теку" branch has a
# non-empty case to confirm — mirrors the mockup's `CLAUDE_FOLDERS`. Every
# other `FS_FIXTURE` path previews as empty (a fresh folder to build into).
_AGENT_ADOPTABLE: dict[str, dict[str, Any]] = {
    "/home/dev/projects/legacy-tool": {
        "has_claude_md": True,
        "claude_md_excerpt": "# legacy-tool\n\nAgent for triaging legacy-tool issues.\n",
        "has_mcp_json": True,
        "mcp_server_names": ["mnemo-memory", "github"],
        "has_claude_dir": True,
        "rule_files": ["v3-build.md"],
        "skill_dirs": ["code-review"],
        "has_memory": True,
        "memory_already_bank": False,
    },
}


def _agent_preview(root: str) -> dict[str, Any]:
    """Fixture mirror of `agent_registry.preview_adopt()` — a read-only
    inspection of a candidate folder, keyed off the same `FS_FIXTURE` the
    bank picker already browses."""
    target = (root or "").strip().replace("\\", "/").rstrip("/") or root
    root_exists = target in FS_FIXTURE
    adopt_info = _AGENT_ADOPTABLE.get(target)
    with _lock:
        already = next((a["slug"] for a in AGENTS if a["root"].rstrip("/") == target), None)
    empty = root_exists and not FS_FIXTURE.get(target) and adopt_info is None
    suggested_name = target.rsplit("/", 1)[-1] or target
    suggested_slug = _unique_agent_slug(_agent_slugify(suggested_name))
    base: dict[str, Any] = {
        "root_exists": root_exists,
        "empty": empty,
        "already_registered_agent": already,
        "has_claude_md": False,
        "claude_md_excerpt": None,
        "has_mcp_json": False,
        "mcp_server_names": [],
        "has_claude_dir": False,
        "rule_files": [],
        "skill_dirs": [],
        "has_memory": False,
        "memory_already_bank": False,
        "suggested_slug": suggested_slug,
        "suggested_name": suggested_name,
    }
    if adopt_info:
        base.update(adopt_info)
        base["empty"] = False
    return base


_LAUNCH_MODES = frozenset({"standard", "custom"})
_LAUNCH_CUSTOM_FIELDS = frozenset({"mode", "host", "port", "model", "autocompact", "extra_args"})


def _validate_launch_config(data: Any) -> dict[str, Any] | None:
    """Fixture mirror of `agent_registry.validate_launch_config` — returns
    the normalized dict, or `None` on anything invalid (the caller turns
    that into a 400, same as the real endpoint's `InvalidLaunchConfig`)."""
    if not isinstance(data, dict):
        return None
    mode = data.get("mode")
    if mode not in _LAUNCH_MODES:
        return None
    if mode == "standard":
        return {"mode": "standard"} if not (set(data) - {"mode"}) else None
    if set(data) - _LAUNCH_CUSTOM_FIELDS:
        return None
    host = data.get("host")
    if not isinstance(host, str) or not host.strip():
        return None
    port = data.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not (1 <= port <= 65535):
        return None
    result: dict[str, Any] = {"mode": "custom", "host": host, "port": port}
    if data.get("model"):
        result["model"] = data["model"]
    if "autocompact" in data:
        result["autocompact"] = data["autocompact"]
    if "extra_args" in data:
        result["extra_args"] = data["extra_args"]
    return result


# --------------------------------------------------------------------------
# self-update fixture state (contract: src/api.py GET/POST /api/update/*,
# WS `update_progress` from `engine_update._emit_progress`; step 11, ui-dev)
# --------------------------------------------------------------------------
#
# Starts with an update already available so the sidebar banner is on screen
# without extra setup. `simulate_update_apply()` below never downloads,
# builds a venv or restarts anything real — it walks the same event sequence
# the real backend does (WS `update_progress` for download/venv/done, then a
# `switching` window with every open socket forcibly closed, matching the
# real backend stopping itself to switch versions) purely in memory, so the
# frontend's poll-plus-WS-disconnect handling is exercisable without a real
# release or a real service restart.
#
# `tag` containing "fail" (case-insensitive) simulates a forced rollback —
# the one path `topics/engine-self-update-design.md`'s point 5 calls out by
# name ("проблема, відкотили назад") and the one this fixture exists to make
# reachable from the browser.

_UPDATE_LOCK = threading.RLock()
_UPDATE_STATE: dict[str, Any] = {
    "current_tag": "v3.1.0",
    "current_installed_at": "2026-08-15T10:00:00+03:00",
    "current_commit": "abc1234",
    "latest_tag": "v3.2.0",
    "checked_at": "2026-08-20T09:00:00+03:00",
    "update_available": True,
    "check_error": None,
    "apply": {
        "state": "idle", "tag": None, "step": None, "error": None,
        "started_at": None, "finished_at": None,
    },
    "history": [
        {"tag": "v3.1.0", "installed_at": "2026-08-15T10:00:00+03:00",
         "commit": "abc1234", "status": "active"},
        {"tag": "v3.0.0", "installed_at": "2026-07-01T10:00:00+03:00",
         "commit": "def5678", "status": "previous"},
    ],
}


def update_status_payload() -> dict:
    """Mirror of `GET /api/update/status`'s shape (`api_update_status`)."""
    with _UPDATE_LOCK:
        s = _UPDATE_STATE
        return {
            "current": {"tag": s["current_tag"], "installed_at": s["current_installed_at"],
                        "commit": s["current_commit"]},
            "latest_known": {"tag": s["latest_tag"], "checked_at": s["checked_at"],
                             "update_available": bool(s["update_available"])},
            "check": {"in_progress": False, "error": s["check_error"]},
            "apply": dict(s["apply"]),
            "history": [dict(e) for e in s["history"]],
            "retention": {"keep": 3},
            "auto": _auto_status_view(),
        }


# --------------------------------------------------------------------------
# unattended auto-apply fixture state (contract: src/api.py POST /api/update/
# auto/confirm|cancel, the "auto" block of GET /api/update/status, WS
# `update_auto_pending`; backend landed in 4f977b6, this is the frontend's
# step, ui-dev)
# --------------------------------------------------------------------------
#
# `_AUTO_COUNTDOWN_S` mirrors the real default (`config.UPDATE_AUTO_APPLY_
# COUNTDOWN_S`). A background thread (`_auto_pending_scheduler`, started from
# `main()`) arms one countdown shortly after boot — the fixture's stand-in for
# the real checker's background tick discovering an eligible tag — so the WS
# event, the countdown modal, and both new endpoints are all reachable
# without any extra setup, the same reasoning `_UPDATE_STATE` already starts
# with `update_available: True` for.

_AUTO_COUNTDOWN_S = 10
_AUTO_STATE: dict[str, Any] = {"pending": None, "timer": None}


def _auto_status_view() -> dict:
    """Mirror of `api._auto_status_view` — `seconds_left` computed here, at
    response time, not trusted from a client-side timer (same "status-poll is
    the truth" discipline `_apply_view`/`update_status_payload` already
    follow)."""
    with _UPDATE_LOCK:
        pending = dict(_AUTO_STATE["pending"]) if _AUTO_STATE["pending"] else None
    if pending is not None:
        deadline_dt = datetime.fromisoformat(pending["deadline"])
        seconds_left = max(0, int((deadline_dt - datetime.now(timezone.utc).astimezone()).total_seconds()))
        pending = {**pending, "seconds_left": seconds_left}
    return {
        "enabled": bool(_SETTINGS.get("auto_update", True)),
        "pending": pending,
        "blacklist": [],
    }


def _arm_auto_pending(tag: str) -> None:
    """Arm a countdown for `tag`, unless one is already pending. Mirrors
    `api.maybe_begin_auto_apply`: flips in-memory state and starts a
    `threading.Timer`, the actual (simulated) apply happens later, off this
    call, when the timer fires or a confirm arrives."""
    with _UPDATE_LOCK:
        if _AUTO_STATE["pending"] is not None:
            return
        if _UPDATE_STATE["apply"]["state"] not in ("idle", "done", "failed", "rolled_back"):
            return
        deadline = (
            datetime.now(timezone.utc).astimezone() + timedelta(seconds=_AUTO_COUNTDOWN_S)
        ).isoformat(timespec="milliseconds")
        _AUTO_STATE["pending"] = {"tag": tag, "started_at": now_iso(), "deadline": deadline}
        timer = threading.Timer(_AUTO_COUNTDOWN_S, _fire_auto_pending, args=(tag,))
        timer.daemon = True
        _AUTO_STATE["timer"] = timer
        timer.start()
    broadcast("update_auto_pending", None, {
        "phase": "started", "tag": tag, "deadline": deadline, "seconds": _AUTO_COUNTDOWN_S,
    })


def _settle_auto_pending(tag: str) -> None:
    """Cancel the countdown (idempotent) and start the same simulated apply
    `POST /api/update/apply` uses. Shared by the timer firing on its own and
    `POST /api/update/auto/confirm` arriving first — mirrors `api._settle_
    auto_pending`'s race guard (whichever gets the lock first proceeds, the
    other sees `pending` already `None`)."""
    with _UPDATE_LOCK:
        if _AUTO_STATE["pending"] is None:
            return
        timer = _AUTO_STATE["timer"]
        _AUTO_STATE["pending"] = None
        _AUTO_STATE["timer"] = None
        _UPDATE_STATE["apply"] = {
            "state": "staging", "tag": tag, "step": "download", "error": None,
            "started_at": now_iso(), "finished_at": None,
        }
    if timer is not None:
        timer.cancel()
    threading.Thread(target=simulate_update_apply, args=(tag,), daemon=True).start()


def _fire_auto_pending(tag: str) -> None:
    _settle_auto_pending(tag)


def _rearm_if_enabled(tag: str) -> None:
    """Dev-only: re-arm shortly after a cancel, mirroring the real backend's
    "no cooldown -- the same tag reappears on the next checker tick" (design
    decision, `api.api_update_auto_cancel`'s own docstring), just faster —
    so cancel/confirm is exercisable more than once without restarting the
    fixture."""
    if _SETTINGS.get("auto_update", True):
        _arm_auto_pending(tag)


def _auto_pending_scheduler() -> None:
    time.sleep(6.0)
    with _UPDATE_LOCK:
        tag = _UPDATE_STATE["latest_tag"]
    _rearm_if_enabled(tag)


def _force_disconnect_all() -> None:
    """Sever every open WS connection right now — the fixture's stand-in for
    the real backend stopping itself mid-`update-apply` (design topic's
    documented trap, confirmed in `_run_staged_apply`/`_spawn_update_apply_
    breakaway`). Closing the raw socket from this thread while the handler
    thread blocks on it is what makes that thread's `read_frame` fail and
    the connection tear down — the same shape a real process death produces
    from the browser's point of view."""
    with _clients_lock:
        targets = list(CLIENTS)
        CLIENTS.clear()
    for client in targets:
        client.alive = False
        try:
            client.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            client.conn.close()
        except OSError:
            pass


def simulate_update_apply(tag: str) -> None:
    """Fixture body for `POST /api/update/apply`'s background thread —
    mirrors `_run_staged_apply` (staging: download/venv/done over WS) plus
    `update-apply`'s stop/switch/start/health window (simulated by severing
    every socket, then sleeping), never a real download, venv build or
    process restart."""
    broadcast("update_progress", None, {"step": "download", "tag": tag, "detail": None, "error": None})
    time.sleep(1.5)
    broadcast("update_progress", None, {"step": "venv", "tag": tag, "detail": None, "error": None})
    time.sleep(1.5)
    broadcast("update_progress", None, {"step": "done", "tag": tag, "detail": None, "error": None})

    with _UPDATE_LOCK:
        _UPDATE_STATE["apply"]["state"] = "switching"
        _UPDATE_STATE["apply"]["step"] = None

    _force_disconnect_all()
    time.sleep(2.5)  # simulated stop -> repoint junction -> start -> health

    with _UPDATE_LOCK:
        if "fail" in tag.lower():
            _UPDATE_STATE["apply"].update({
                "state": "rolled_back",
                "error": "simulated health check failure (fixture)",
                "finished_at": now_iso(),
            })
            # current_tag is left untouched -- a rollback undoes the switch.
        else:
            for entry in _UPDATE_STATE["history"]:
                if entry["status"] == "active":
                    entry["status"] = "previous"
            commit = "sim" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:7]
            _UPDATE_STATE["history"].insert(0, {
                "tag": tag, "installed_at": now_iso(), "commit": commit, "status": "active",
            })
            _UPDATE_STATE["current_tag"] = tag
            _UPDATE_STATE["current_installed_at"] = now_iso()
            _UPDATE_STATE["current_commit"] = commit
            _UPDATE_STATE["apply"].update({"state": "done", "error": None, "finished_at": now_iso()})


def _settings_presets() -> list[dict]:
    import sys

    sys.path.insert(0, str(HERE.parent.parent))
    from src import presets

    return presets.as_json()


def embed_state_payload() -> dict:
    """Mirror of `/api/embed/state`, driven by the fixture's own provider.

    Both branches are reachable in dev by switching the backend tab and
    saving: `local` reports a resident, an `api` backend whose URL looks like
    Ollama reports a held model, and OpenAI reports that it holds nothing —
    so it gets a probe button but never an unload button.
    """
    provider = _SETTINGS.get("provider") or "local"
    url = (_SETTINGS.get("api") or {}).get("url") or ""
    model = (_SETTINGS.get("api") or {}).get("model") or ""
    if provider == "local":
        out = {
            "backend": "local",
            "model": "intfloat/multilingual-e5-large",
            "holding": _EMBED_HELD["holding"],
            "cached": _MODEL_CACHED["value"],
            "where": "127.0.0.1:4645",
            "wake_s": 7.6,
            # Fixture matches this session's real machine (3h override) so
            # the field renders non-trivially in dev too.
            "idle_timeout_s": 10800,
            "detail": None,
        }
    elif "11434" in url:
        out = {
            "backend": "ollama",
            "model": model or "bge-m3",
            "holding": _EMBED_HELD["holding"],
            "cached": None,
            "where": url,
            "wake_s": 8.4,
            "detail": None,
            # A second model, so the "not ours, left alone" line is visible
            # in dev — it is the whole point of unloading only our own.
            "others_held": 1,
        }
        if _EMBED_HELD["holding"] == "loaded":
            out["expires_at"] = "2026-08-16T23:51:57+02:00"
    else:
        out = {
            "backend": "api",
            "model": model or "text-embedding-3-small",
            "holding": "n/a",
            "cached": None,
            "where": url,
            "wake_s": None,
            # Empty, like the real endpoint: `holding: n/a` is the whole
            # answer and the client words it. `detail` carries only what the
            # service alone knows.
            "detail": None,
        }
    # Only where a probe could have happened. The flag is remembered across
    # requests, so without this guard switching the backend tab carried the
    # last `load`'s result onto an endpoint nothing had called — dev showing
    # evidence of a check that never ran.
    if _EMBED_HELD.get("probe_dim") and out["holding"] in ("loaded", "n/a"):
        out["probe_dim"] = _EMBED_HELD["probe_dim"]
        expected = int((_SETTINGS.get("api") or {}).get("dim") or 1024)
        if out["probe_dim"] != expected:
            out["detail"] = (
                f"the endpoint returned {out['probe_dim']}-wide vectors but "
                f"this machine is configured for {expected}; fix the width "
                "before indexing"
            )
    # Merged in unconditionally, matching `api.py`'s
    # `{**embedctl.state(), "download": _download_status()}` — present for
    # every provider even though only `local`/Ollama can ever have
    # `cached is False` and show the button.
    out["download"] = {"active": _DOWNLOAD["active"], "failed": _DOWNLOAD["failed"]}
    return out


def _finish_download() -> None:
    """Runs on a timer thread: coarse status only, no progress reporting —
    same shape as the real backend's detached `warmup --force`."""
    time.sleep(4.0)
    _MODEL_CACHED["value"] = True
    _DOWNLOAD["active"] = False


def settings_payload() -> dict:
    def item(key: str, value: Any) -> dict:
        env = _SETTINGS_ENV.get(key)
        source = "env" if env else ("file" if value not in ("", 0) else "default")
        return {"value": value, "source": source, "env_var": env or "",
                "overridden": bool(env)}

    api = _SETTINGS["api"]
    return {
        "path": "/home/dev/.mnemo/state/settings.json",
        "exists": True,
        "settings": {
            "provider": item("provider", _SETTINGS["provider"]),
            "auto_update": item("auto_update", _SETTINGS["auto_update"]),
            "api.url": item("api.url", api["url"]),
            "api.model": item("api.model", api["model"]),
            "api.dim": item("api.dim", api["dim"]),
            "api.timeout": item("api.timeout", api["timeout"]),
            "api.key_set": item("api.key_set", bool(api["key"])),
        },
        "readonly": {"api_host": "127.0.0.1", "api_port": 4646},
        "presets": _settings_presets(),
    }


def doctor_payload() -> dict[str, Any]:
    """Fixture of the one structured report shared by CLI and API."""
    machine = _SETTINGS.get("provider") or "local"
    overrides = sorted({
        bank.get("provider")
        for bank in BANKS
        if bank.get("provider") and bank.get("provider") != machine
    })
    local_in_use = "local" in {machine, *overrides}
    api = _SETTINGS.get("api") or {}
    endpoint = {"applicable": "api" in {machine, *overrides}}
    if endpoint["applicable"]:
        configured = bool(api.get("url") and api.get("model") and api.get("dim"))
        endpoint.update({
            "configured": configured,
            "url": api.get("url"),
            "model": api.get("model"),
            "dim": api.get("dim"),
            "key_set": bool(api.get("key")),
            "error": None if configured else "url, model and dim are required",
        })

    return {
        "engine": {
            "home": "/home/dev/.mnemo",
            "state_dir": "/home/dev/.mnemo/state",
            "python": "/home/dev/.mnemo/.venv/bin/python",
        },
        "provider": {
            "machine": machine,
            "overrides": overrides,
            "local_in_use": local_in_use,
        },
        "model": {"cached": True, "needed": local_in_use},
        "sqlite_vec": {"ok": True, "error": None},
        "resident": {
            "applicable": local_in_use,
            "up": True if local_in_use else None,
            "host": "127.0.0.1",
            "port": 4645,
            "scope": "machine_port",
        },
        "endpoint": endpoint,
        "backend": {
            "up": True,
            "url": "http://127.0.0.1:8919",
            "scope": "machine_port",
            "error": None,
            "serving_pid": 0,
            "launcher_pid": None,
            "banks": len(BANKS),
            "queue_depth": queue_snapshot()["depth"],
        },
        "token": {
            "present": True,
            "source": "fixture",
            "where": "dev fixture token",
            "scope": "fixture",
        },
        "registry": {
            "ok": True,
            "error": None,
            "count": len(BANKS),
            "banks": [
                {
                    "id": bank["id"],
                    "name": bank["name"],
                    "root": bank["root"],
                    "state": bank.get("state") or "enabled",
                    "exists": bank.get("exists", True),
                }
                for bank in BANKS
            ],
        },
        "orphans": {
            "ok": True,
            "error": None,
            "count": len(_ORPHANS),
            "bytes": sum(item["size"] for item in _ORPHANS),
            "items": [dict(item) for item in _ORPHANS],
        },
        "wiring": {
            "ok": True,
            "error": None,
            "total": 3,
            "stale": [
                {
                    "root": "/home/dev/projects/old-memory",
                    "command": "mnemo init --migrate --root \"/home/dev/projects/old-memory\"",
                    "reason": ".mcp.json: mnemo is a legacy stdio entry",
                    "migrate": True,
                }
            ],
        },
    }


# --------------------------------------------------------------------------
# fixture bank tokens (contract 9.5 GET/POST /api/banks/{bank_id}/token)
# --------------------------------------------------------------------------
#
# Derived from the bank id, never read from the service's own state. The same
# reasoning as FS_FIXTURE above, one step sharper: these are credentials. A dev
# mock that reached for a real per-bank token would put a live secret behind a
# page that is still being written, and a UI mistake here would leak it. Being
# derived they are also stable across restarts, so a dev page keeps working
# after this server is bounced.

BANK_TOKENS: dict[str, str] = {}


def bank_token(bank_id: str) -> str:
    with _lock:
        token = BANK_TOKENS.get(bank_id)
        if token is None:
            token = hashlib.sha256(
                f"mnemo-dev-bank-token:{bank_id}".encode("utf-8")
            ).hexdigest()[:48]
            BANK_TOKENS[bank_id] = token
        return token


def regenerate_bank_token(bank_id: str) -> str:
    """A new value, still invented — `secrets` would be no more real here."""
    with _lock:
        token = hashlib.sha256(
            f"mnemo-dev-bank-token:{bank_id}:{time.time_ns()}".encode("utf-8")
        ).hexdigest()[:48]
        BANK_TOKENS[bank_id] = token
        return token


def _memory_dir_for(target: str) -> str:
    """Mirrors `api._memory_dir_for`: never doubles a `.claude` already
    picked. `target` already has no trailing slash here."""
    if target.endswith("/.claude/memory"):
        return target
    if target.endswith("/.claude") or target == ".claude":
        return target + "/memory"
    return target + "/.claude/memory"


def fs_listing(path: str | None) -> dict | None:
    """One directory listing, or ``None`` when the fixture has no such path."""
    target = (path or "").strip().replace("\\", "/") or FS_HOME
    if len(target) > 1:
        target = target.rstrip("/")
    if target not in FS_FIXTURE:
        return None
    parent = target.rsplit("/", 1)[0] or "/"
    with _lock:
        registered = {b["root"].rstrip("/"): b["name"] for b in BANKS}
    entries = [
        {"name": name,
         "path": (target.rstrip("/") + "/" + name),
         "registered": registered.get(target.rstrip("/") + "/" + name)}
        for name in sorted(FS_FIXTURE[target], key=str.lower)
    ]
    memory_dir = _memory_dir_for(target)
    return {
        "path": target,
        "display": target,
        "parent": None if target == "/" else parent,
        "home": FS_HOME,
        "roots": [{"name": "/", "path": "/"}],
        "registered": registered.get(target),
        "md": FS_MD.get(target, 0),
        "md_capped": False,
        "entries": entries,
        "truncated": False,
        # MN-25: where "create structure here" would land, and whether it's
        # already there — mirrors `api._memory_dir_for`/`api_fs_dirs`.
        "memory_dir": memory_dir,
        "has_claude_memory": memory_dir != target and memory_dir in FS_FIXTURE,
    }


# --------------------------------------------------------------------------
# WebSocket plumbing (RFC 6455, the small subset a browser needs)
# --------------------------------------------------------------------------


class WSClient:
    def __init__(self, conn, bank_filter: str | None) -> None:
        self.conn = conn
        self.bank_filter = bank_filter
        self.send_lock = threading.Lock()
        self.alive = True

    def send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length < 1 << 16:
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        with self.send_lock:
            self.conn.sendall(bytes(header) + payload)

    def send_json(self, obj: dict) -> None:
        self.send_frame(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


CLIENTS: list[WSClient] = []
_clients_lock = threading.Lock()


def broadcast(event_type: str, bank_id: str | None, data: dict) -> None:
    envelope = {
        "v": 1,
        "type": event_type,
        "ts": now_iso_ms(),
        "bank_id": bank_id,
        "data": data,
    }
    with _clients_lock:
        targets = list(CLIENTS)
    for client in targets:
        if client.bank_filter and bank_id and client.bank_filter != bank_id:
            continue
        try:
            client.send_json(envelope)
        except OSError:
            client.alive = False


def read_frame(rfile) -> tuple[int, bytes] | None:
    head = rfile.read(2)
    if len(head) < 2:
        return None
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rfile.read(8))[0]
    mask = rfile.read(4) if masked else b""
    payload = rfile.read(length) if length else b""
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def queue_snapshot() -> dict:
    with _lock:
        return dict(QUEUE)


def set_queue(**fields) -> None:
    with _lock:
        QUEUE.update(fields)
    broadcast("queue", None, queue_snapshot())


# --------------------------------------------------------------------------
# simulated indexing
# --------------------------------------------------------------------------


def _task_id() -> str:
    global _task_seq
    with _lock:
        _task_seq += 1
        return f"{_task_seq:04x}" + "c1e" + f"{int(time.time()) % 0x10000:04x}"


def _log_index(bank_id: str, **fields) -> dict:
    global _next_index_id
    with _lock:
        row = {
            "id": _next_index_id,
            "ts": now_iso(),
            "bank_id": bank_id,
            "kind": fields.get("kind", "file"),
            "trigger": fields.get("trigger", "ui"),
            "path": fields.get("path"),
            "result": fields.get("result", "ok"),
            "files_indexed": fields.get("files_indexed", 0),
            "chunks_indexed": fields.get("chunks_indexed", 0),
            "files_pruned": fields.get("files_pruned", 0),
            "took_ms": fields.get("took_ms"),
            "error": fields.get("error"),
        }
        _next_index_id += 1
        INDEX_EVENTS.insert(0, row)
    return row


def _fixture_provider_key(bank: dict) -> tuple[str, str]:
    chosen = bank.get("provider") or _SETTINGS.get("provider") or "local"
    if chosen == "local":
        return "local", "local:intfloat/multilingual-e5-large:1024"
    api = _SETTINGS.get("api") or {}
    model = api.get("model") or "unconfigured"
    dim = api.get("dim") or 0
    return "api", f"api:{model}:{dim}"


def bank_info(bank: dict) -> dict:
    """BankInfo (contract 9.5), including active vs indexed provider."""
    with _lock:
        out = dict(bank)
        active, key = _fixture_provider_key(bank)
        indexed = out.get("index_provider_key")
        out["provider_active"] = active
        out["provider_key"] = key
        out["rebuild_pending"] = bool(indexed and indexed != key)
        out["provider_error"] = None
        return out


def _mock_init_result(root: str) -> dict:
    """Fixture-only stand-in for what `POST /api/banks`'s optional `init`
    would report from a real `mnemo init --root <project>` run (contract:
    `{"ok": true, "log": [...]}` or `{"ok": false, "skipped": true,
    "reason": ...}`). Never runs `mnemo init` for real — this server answers
    shapes, not the product — so the log is canned, picked by project root
    to keep both real-world outcomes reachable from the picker.
    """
    suffix = "/.claude/memory"
    if not root.endswith(suffix):
        # Only reachable if a client sent `init: true` for an ineligible
        # root — the picker's checkbox stays disabled+unchecked for those,
        # but the server does not trust the client to have honoured that.
        return {"ok": False, "skipped": True,
                "reason": "bank root is not <project>/.claude/memory"}
    project = root[: -len(suffix)]
    if project.endswith("voice-agent"):
        return {"ok": True, "log": [
            ".mcp.json уже відстежується git — mnemo не переписує токен "
            "у трекований файл",
            "виконайте вручну: git rm --cached .mcp.json",
            "після цього запустіть mnemo init ще раз",
        ]}
    return {"ok": True, "log": [
        "додано .mcp.json.template, .mcp.env, mcp-setup.sh/.ps1",
        "зареєстровано сервер mnemo-memory з токеном цього банку",
        "готово — .mcp.json підключено",
    ]}


def _find_tree_node(node: dict, rel: str) -> dict | None:
    """Locate a file node in a tree fixture by its bank-relative path."""
    if node.get("type") == "file" and node.get("path") == rel:
        return node
    for child in node.get("children", []):
        found = _find_tree_node(child, rel)
        if found is not None:
            return found
    return None


def mark_indexed(bank_id: str, rel: str) -> int:
    """Move a file into the index, fixture-side.

    The real backend flips `indexed` and the chunk count the moment a file
    task commits, and `/api/tree` reports it on the next read. A simulator
    that never changes state cannot show whether the console keeps up, so it
    changes state here too. A file that had no chunks gets one covering the
    whole text, which is coherent for both the tree and the file view.
    """
    with _lock:
        record = FILES.get(bank_id, {}).get(rel)
        if record is None:
            return 0
        if not record["chunks"]:
            record["chunks"] = [{
                "chunk_uid": f"{abs(hash(rel)) & 0xffffffffffff:012x}",
                "chunk_index": 0,
                "heading": None,
                "start_char": 0,
                "end_char": len(record.get("text", "")),
            }]
        record["indexed"] = True
        count = len(record["chunks"])
        tree = TREES.get(bank_id)
        node = _find_tree_node(tree["tree"], rel) if tree else None
        if node is not None:
            node["indexed"] = True
            node["chunks"] = count
        return count


# MN-15: relpaths queued or in flight right now, per bank. Fixture-only
# mirror of `workqueue.pending_paths()` — this mock has no real queue, so
# `simulate_task` below is the one place that adds to and removes from it,
# same as it is the one place that already fabricates `index_start`/
# `index_progress`/`index_done` for a reindex.
_PENDING: dict[str, set[str]] = {}


def _tree_response(bank_id: str) -> dict:
    """`/api/tree`'s fixture, with the live `pending` field merged in.

    `TREES[bank_id]` is the static fixture loaded once at startup; `pending`
    is the one field on it that changes while the server runs, so it is
    computed here rather than mutated onto the shared fixture dict.
    """
    data = dict(TREES[bank_id])
    with _lock:
        data["pending"] = sorted(_PENDING.get(bank_id, ()))
    return data


def simulate_task(bank: dict, *, kind: str, path: str | None, batch_ms: int) -> None:
    """Walk one reindex through the WS event sequence of contract 9.7."""
    bank_id = bank["id"]
    files = FILES.get(bank_id, {})
    # Every `.md` in the bank, not just the ones already in the index — that
    # is what a real bulk covers, and a file joining the index is precisely
    # the transition the tree has to keep up with.
    targets = [path] if path else list(files)
    started = time.time()

    # MN-15: a real bulk scan enqueues every file up front, then a worker
    # pulls them one at a time — mirrored here so `file_queued` (and the
    # `pending` field `_tree_response` serves meanwhile) both show every
    # target highlighted from the start, not just the one being processed.
    with _lock:
        _PENDING.setdefault(bank_id, set()).update(targets)
    for rel in targets:
        broadcast("file_queued", bank_id, {"path": rel})

    with _lock:
        bank["status"] = "indexing"
        bank["indexing"] = True
        bank["queued"] = len(targets)
        bank["last_error"] = None
    broadcast("bank_status", bank_id, {"bank": bank_info(bank)})
    set_queue(depth=len(targets), high=0, normal=1 if path else 0,
              low=0 if path else len(targets), current=None)

    # A bulk finishes when its *scan* finishes, not when the file tasks it
    # spawned do — `_scan_bank_streaming` reports `index_done` and leaves the
    # per-file work behind it in the queue. Emitting it here rather than at
    # the end is what the real backend does, and the ordering matters: a
    # client that keys progress by bank alone sees this land mid-file.
    if not path:
        broadcast("index_done", bank_id,
                  {"task_id": _task_id(), "kind": kind, "path": None,
                   "chunks_indexed": 0, "files_indexed": 0,
                   "planned_files": len(targets), "planned_prunes": 0,
                   "took_ms": (time.time() - started) * 1000.0})

    total_chunks = 0
    for position, rel in enumerate(targets):
        record = files.get(rel)
        chunk_total = len(record["chunks"]) if record and record["chunks"] else 1
        batches = max(1, (chunk_total + 1) // 2)
        task_id = _task_id()

        set_queue(depth=len(targets) - position, high=0,
                  normal=1 if path else 0, low=0 if path else len(targets) - position,
                  # `queue.current.started_at` is absolute EPOCH SECONDS, not
                  # an ISO string like the service's own `started_at` above —
                  # the console computes elapsed time from it at render time.
                  current={"task_id": task_id, "bank_id": bank_id, "kind": "file",
                           "path": rel, "batch": 0, "batches": batches,
                           "started_at": time.time()})
        broadcast("index_start", bank_id, {"task_id": task_id, "kind": "file",
                                           "path": rel, "batches": batches,
                                           "trigger": "ui"})
        done = 0
        for batch in range(1, batches + 1):
            time.sleep(batch_ms / 1000.0)
            done = min(chunk_total, done + 2)
            broadcast("index_progress", bank_id,
                      {"task_id": task_id, "path": rel, "batch": batch,
                       "batches": batches, "chunks_done": done,
                       "chunks_total": chunk_total})

        chunk_total = mark_indexed(bank_id, rel) or chunk_total
        total_chunks += chunk_total
        with _lock:
            _PENDING.get(bank_id, set()).discard(rel)
        took = (time.time() - started) * 1000.0
        broadcast("index_done", bank_id,
                  {"task_id": task_id, "kind": "file", "path": rel,
                   "chunks_indexed": chunk_total, "files_indexed": 1, "took_ms": took})
        _log_index(bank_id, kind="file", trigger="ui", path=rel, result="ok",
                   files_indexed=1, chunks_indexed=chunk_total, took_ms=took)

        with _lock:
            bank["queued"] = len(targets) - position - 1
        broadcast("bank_status", bank_id, {"bank": bank_info(bank)})

    took = (time.time() - started) * 1000.0
    with _lock:
        bank["status"] = "ready" if total_chunks else "empty"
        bank["indexing"] = False
        bank["queued"] = 0
        bank["last_indexed"] = now_iso()
        if not path:
            bank["chunks"] = total_chunks
            bank["files"] = len(files)  # every .md in the bank, indexed or not
            # A completed rebuild now carries the active provider identity, so
            # the console's REBUILD PENDING banner has a real transition to
            # observe rather than a fixture that warns forever.
            bank["index_provider_key"] = _fixture_provider_key(bank)[1]
    set_queue(depth=0, high=0, normal=0, low=0, current=None)
    broadcast("bank_status", bank_id, {"bank": bank_info(bank)})

    if not path:
        # The bulk's own `index_done` already went out when the scan ended;
        # only the journal row is owed here.
        _log_index(bank_id, kind=kind, trigger="ui", path=None, result="ok",
                   files_indexed=len(targets), chunks_indexed=total_chunks,
                   took_ms=took)


# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mnemo-mock/0"
    batch_ms = 260

    # -- helpers ---------------------------------------------------------

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib name
        print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json_out(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def fail(self, status: int, code: str, message: str, detail: Any = None) -> None:
        self.json_out(status, {"error": {"code": code, "message": message,
                                         "detail": detail}})

    def authorised(self) -> bool:
        if NO_AUTH:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[7:].strip() == DEV_TOKEN
        return self.headers.get("X-Mnemo-Token", "").strip() == DEV_TOKEN

    # -- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/ws":
            self.handle_ws(query)
            return
        if path == "/health":
            self.json_out(200, self.health())
            return
        if path == "/" or path == "/ui":
            self.send_response(302)
            self.send_header("Location", "/ui/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/ui"):
            self.serve_static(path)
            return
        if path.startswith("/api/"):
            if not self.authorised():
                self.fail(401, "unauthorized", "missing or invalid token")
                return
            self.handle_api_get(path, query)
            return
        self.fail(404, "internal", "no route " + path)

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib name
        self.do_GET()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        if not self.authorised():
            self.fail(401, "unauthorized", "missing or invalid token")
            return

        if parsed.path.startswith("/api/catalog/"):
            self.delete_catalog_entry(unquote(parsed.path[len("/api/catalog/"):]))
            return

        if parsed.path.startswith("/api/agents/"):
            slug_ref = unquote(parsed.path[len("/api/agents/"):])
            with _lock:
                before = len(AGENTS)
                AGENTS[:] = [a for a in AGENTS if a["slug"] != slug_ref]
                removed = len(AGENTS) != before
            if not removed:
                self.fail(404, "agent_not_found", "no agent matches", {"slug": slug_ref})
                return
            self.json_out(200, {"ok": True})
            return

        if not parsed.path.startswith("/api/banks/"):
            self.fail(404, "internal", "no route " + parsed.path)
            return

        ref = unquote(parsed.path[len("/api/banks/"):])
        bank = find_bank(ref)
        if not bank:
            self.fail(404, "bank_not_found", "no bank matches", {"ref": ref})
            return

        # `drop_index` defaults to true, exactly as the real endpoint does.
        query = parse_qs(parsed.query)
        drop = (query.get("drop_index", ["true"])[0] or "true").lower() != "false"
        strip_mcp = (query.get("strip_mcp", ["false"])[0] or "false").lower() == "true"

        # The one failure the dialog has to render in place. A fixture cannot
        # hold a real file lock, so a bank still indexing stands in for it —
        # that is the same condition the backend refuses on, and it keeps the
        # error path reachable in dev.
        if bank.get("indexing") or bank.get("queued"):
            self.fail(409, "index_locked",
                      f"bank {bank['name']!r} is still being indexed; "
                      f"try again in a moment", {"bank_id": bank["id"]})
            return

        # `mcp_stripped` only ever appears in the response when `strip_mcp`
        # was actually requested (MN-13 contract) — the UI never sends it
        # unless the checkbox was offered *and* checked, so the 400 here
        # stands in for a request made by hand against a non-project bank.
        mcp_stripped: list[str] | None = None
        if strip_mcp:
            project_root = _project_root_for_bank(bank)
            if project_root is None:
                self.fail(400, "bad_request",
                          "bank root does not resolve to a project — nothing to strip",
                          {"bank_id": bank["id"]})
                return
            info = _MCP_WIRING.get(bank["id"], {"has_wiring": False, "uses_template": False})
            if info["uses_template"]:
                mcp_stripped = [".mcp.json.template", ".mcp.env"]
            elif info["has_wiring"]:
                mcp_stripped = [".mcp.json"]
            else:
                mcp_stripped = []

        with _lock:
            BANKS[:] = [b for b in BANKS if b["id"] != bank["id"]]
        broadcast("bank_removed", bank["id"], {"bank_id": bank["id"]})
        response: dict = {"ok": True, "index_removed": drop}
        if strip_mcp:
            response["mcp_stripped"] = mcp_stripped
        self.json_out(200, response)

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        if not self.authorised():
            self.fail(401, "unauthorized", "missing or invalid token")
            return

        if parsed.path.startswith("/api/catalog/"):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self.fail(422, "validation_error", "body is not valid JSON")
                return
            self.patch_catalog_entry(unquote(parsed.path[len("/api/catalog/"):]), body)
            return

        if not parsed.path.startswith("/api/banks/"):
            self.fail(404, "internal", "no route " + parsed.path)
            return

        ref = unquote(parsed.path[len("/api/banks/"):])
        bank = find_bank(ref)
        if not bank:
            self.fail(404, "bank_not_found", "no bank matches", {"ref": ref})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self.fail(422, "validation_error", "body is not valid JSON")
            return

        state = body.get("state")
        if state is None:
            self.fail(400, "bad_request", "nothing to change")
            return
        if state not in ("enabled", "frozen", "disabled"):
            self.fail(400, "bad_request",
                      f"unknown state {state!r} — expected one of "
                      f"enabled, frozen, disabled")
            return

        with _lock:
            bank["state"] = state
            # Derived on the real side too, and the card still reads it.
            bank["enabled"] = state == "enabled"
        broadcast("bank_status", bank["id"], {"bank": bank_info(bank)})
        self.json_out(200, bank_info(bank))

    def do_PUT(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        if not self.authorised():
            self.fail(401, "unauthorized", "missing or invalid token")
            return

        if parsed.path.startswith("/api/agents/") and parsed.path.endswith("/launch"):
            slug_ref = unquote(parsed.path[len("/api/agents/"): -len("/launch")])
            agent = _find_agent(slug_ref)
            if agent is None:
                self.fail(404, "agent_not_found", "no agent matches", {"slug": slug_ref})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self.fail(422, "validation_error", "body is not valid JSON")
                return
            normalized = _validate_launch_config(body)
            if normalized is None:
                self.fail(400, "invalid_launch_config", "invalid launch config", {"slug": slug_ref})
                return
            with _lock:
                agent["launch"] = normalized
            self.json_out(200, dict(normalized))
            return

        if parsed.path != "/api/settings":
            self.fail(404, "internal", "no route " + parsed.path)
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self.fail(422, "validation_error", "body is not valid JSON")
            return

        # The same validation the real endpoint applies, and for the same
        # reason: a dev page that could save a zero `dim` would look correct
        # while the product refuses it, and the form's error path would never
        # be seen until it hit a real service.
        if "provider" in body:
            chosen = str(body["provider"] or "").strip().lower()
            if chosen not in ("local", "api"):
                self.fail(400, "bad_request",
                          f"unknown provider {chosen!r} (known: local, api)")
                return
            _SETTINGS["provider"] = chosen
        if "auto_update" in body:
            _SETTINGS["auto_update"] = bool(body["auto_update"])
        api_in = body.get("api")
        if isinstance(api_in, dict):
            for key in ("url", "model"):
                if key in api_in:
                    _SETTINGS["api"][key] = str(api_in[key] or "")
            if "key" in api_in:
                _SETTINGS["api"]["key"] = str(api_in["key"] or "")
            if "dim" in api_in:
                try:
                    _SETTINGS["api"]["dim"] = max(0, int(api_in["dim"] or 0))
                except (TypeError, ValueError):
                    self.fail(400, "bad_request", "dim must be a whole number")
                    return
            if "timeout" in api_in:
                try:
                    _SETTINGS["api"]["timeout"] = float(api_in["timeout"] or 60.0)
                except (TypeError, ValueError):
                    self.fail(400, "bad_request", "timeout must be a number")
                    return

        self.json_out(200, {"ok": True, "restart_required": False,
                            **settings_payload()})

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.fail(404, "internal", "no route " + parsed.path)
            return
        if not self.authorised():
            self.fail(401, "unauthorized", "missing or invalid token")
            return

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            self.fail(422, "validation_error", "body is not valid JSON")
            return

        if parsed.path == "/api/reindex":
            self.post_reindex(body)
            return
        if parsed.path == "/api/banks":
            self.post_bank(body)
            return
        if parsed.path == "/api/catalog":
            self.post_catalog_entry(body)
            return
        if parsed.path == "/api/agents/preview":
            root = str(body.get("root") or "").strip()
            if not root:
                self.fail(400, "bad_request", "'root' is required")
                return
            self.json_out(200, _agent_preview(root))
            return
        if parsed.path == "/api/agents":
            self.post_agent(body)
            return
        if parsed.path == "/api/autostart":
            if "enabled" not in body:
                self.fail(400, "bad_request", "expected 'enabled'")
                return
            _AUTOSTART["enabled"] = bool(body["enabled"])
            self.json_out(200, dict(_AUTOSTART))
            return
        if parsed.path == "/api/clean-orphans":
            requested = list(dict.fromkeys(str(item) for item in body.get("ids", []) if item))
            if not requested:
                self.fail(400, "bad_request", "expected at least one orphan index id")
                return
            removed, skipped, locked, freed = [], [], [], 0
            with _lock:
                current = {item["id"]: item for item in _ORPHANS}
                for index_id in requested:
                    item = current.get(index_id)
                    if item is None:
                        skipped.append({"id": index_id,
                                        "reason": "not in the current orphan list"})
                        continue
                    # One stubborn sidecar makes the partial-result branch
                    # reachable in dev; no real file is touched.
                    if index_id == "facefeedfacefeed":
                        locked.append({
                            "id": index_id,
                            "paths": [item["path"] + "-wal"],
                        })
                        continue
                    _ORPHANS.remove(item)
                    removed.append({"id": index_id, "files_removed": 1,
                                    "bytes": item["size"]})
                    freed += item["size"]
            self.json_out(200, {
                "requested": requested,
                "removed": removed,
                "skipped": skipped,
                "locked": locked,
                "freed_bytes": freed,
            })
            return
        if parsed.path == "/api/embed/download":
            if _MODEL_CACHED["value"]:
                self.fail(409, "already_cached",
                          "the model is already cached — nothing to download")
                return
            if _DOWNLOAD["active"]:
                self.fail(409, "download_in_progress",
                          "a download is already running")
                return
            _DOWNLOAD["active"] = True
            _DOWNLOAD["failed"] = False
            threading.Thread(target=_finish_download, daemon=True).start()
            self.json_out(200, {"started": True})
            return
        if parsed.path in ("/api/embed/unload", "/api/embed/load"):
            state = embed_state_payload()
            if state["holding"] == "n/a" and parsed.path.endswith("/unload"):
                # Hosted endpoints have no memory here to release. `load` is
                # different: it means an explicit probe embedding and is the
                # useful action this branch exists to expose.
                self.fail(502, "embed_control_failed",
                          "this endpoint holds nothing on this machine — "
                          "there is nothing to unload")
                return
            if parsed.path.endswith("/unload"):
                _EMBED_HELD["holding"] = "unloaded"
                _EMBED_HELD["probe_dim"] = None
            else:
                _EMBED_HELD["holding"] = "loaded"
                _EMBED_HELD["probe_dim"] = 1024
            self.json_out(200, embed_state_payload())
            return
        if parsed.path == "/api/update/check":
            with _UPDATE_LOCK:
                _UPDATE_STATE["checked_at"] = now_iso()
                _UPDATE_STATE["update_available"] = (
                    _UPDATE_STATE["latest_tag"] != _UPDATE_STATE["current_tag"]
                )
                out = {
                    "latest_tag": _UPDATE_STATE["latest_tag"],
                    "current_tag": _UPDATE_STATE["current_tag"],
                    "update_available": _UPDATE_STATE["update_available"],
                    "checked_at": _UPDATE_STATE["checked_at"],
                    "error": _UPDATE_STATE["check_error"],
                }
            self.json_out(200, out)
            return

        if parsed.path == "/api/update/apply":
            tag = str(body.get("tag") or "")
            with _UPDATE_LOCK:
                if tag != _UPDATE_STATE["latest_tag"] or not _UPDATE_STATE["update_available"]:
                    self.fail(409, "stale_target",
                              f"{tag!r} does not match the last known latest tag "
                              f"({_UPDATE_STATE['latest_tag']!r}) — run a check again",
                              {"tag": tag, "latest_tag": _UPDATE_STATE["latest_tag"]})
                    return
                if _UPDATE_STATE["apply"]["state"] not in ("idle", "done", "failed", "rolled_back"):
                    self.fail(409, "update_in_progress",
                              f"an update is already {_UPDATE_STATE['apply']['state']} "
                              f"(tag {_UPDATE_STATE['apply'].get('tag')!r})")
                    return
                _UPDATE_STATE["apply"] = {
                    "state": "staging", "tag": tag, "step": "download", "error": None,
                    "started_at": now_iso(), "finished_at": None,
                }
            threading.Thread(target=simulate_update_apply, args=(tag,), daemon=True).start()
            self.json_out(202, {"accepted": True, "tag": tag})
            return

        if parsed.path == "/api/update/auto/confirm":
            with _UPDATE_LOCK:
                pending = dict(_AUTO_STATE["pending"]) if _AUTO_STATE["pending"] else None
            if pending is None:
                self.fail(404, "auto_not_pending", "no auto-apply countdown is pending right now")
                return
            _settle_auto_pending(pending["tag"])
            self.json_out(202, {"accepted": True, "tag": pending["tag"]})
            return

        if parsed.path == "/api/update/auto/cancel":
            with _UPDATE_LOCK:
                pending = _AUTO_STATE["pending"]
                timer = _AUTO_STATE["timer"]
                if pending is not None:
                    _AUTO_STATE["pending"] = None
                    _AUTO_STATE["timer"] = None
            if pending is None:
                self.fail(404, "auto_not_pending", "no auto-apply countdown is pending right now")
                return
            if timer is not None:
                timer.cancel()
            broadcast("update_auto_pending", None, {"phase": "cancelled", "tag": pending["tag"]})
            self.json_out(200, {"accepted": True, "tag": pending["tag"]})
            threading.Timer(15.0, _rearm_if_enabled, args=(pending["tag"],)).start()
            return

        if parsed.path.startswith("/api/banks/") and parsed.path.endswith("/token"):
            ref = parsed.path[len("/api/banks/"): -len("/token")]
            bank = find_bank(unquote(ref))
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches", {"ref": ref})
                return
            self.json_out(200, {"bank_id": bank["id"], "name": bank["name"],
                                "token": regenerate_bank_token(bank["id"])})
            return
        self.fail(404, "internal", "no route " + parsed.path)

    # -- static ----------------------------------------------------------

    def serve_static(self, path: str) -> None:
        rel = unquote(path[len("/ui"):]).lstrip("/")
        target = (STATIC_DIR / rel).resolve() if rel else STATIC_DIR.resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        # Mirrors the real mount's `StaticFiles(..., html=True)`: a request
        # for a directory (the bare root, or any of the exported app's
        # nested routes — `/ui/settings/` resolves to a directory now that
        # each route is its own `out/<route>/index.html`, not one shared
        # `index.html` with in-DOM tab switching) resolves to that
        # directory's own `index.html`, not a 404.
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    # -- api -------------------------------------------------------------

    def health(self) -> dict:
        return {"ok": True, "version": SERVICE_VERSION, "pid": 0, "port": self.server.server_port,
                "uptime_s": round(time.time() - STARTED_AT, 1), "banks": len(BANKS),
                "queue_depth": queue_snapshot()["depth"],
                "embed": {"provider": "local", "reachable": True,
                          "host": "127.0.0.1", "port": 4645}}

    def handle_api_get(self, path: str, query: dict) -> None:
        if path == "/api/embed/state":
            self.json_out(200, embed_state_payload())
            return

        if path == "/api/doctor":
            report = doctor_payload()
            report["backend"]["url"] = (
                f"http://{self.server.server_address[0]}:{self.server.server_port}"
            )
            self.json_out(200, report)
            return

        if path == "/api/banks":
            self.json_out(200, {"banks": [bank_info(b) for b in BANKS]})
            return

        if path.startswith("/api/banks/"):
            # `/token` and `/mcp-wiring` first: the generic bank lookup below
            # would otherwise try to resolve "<id>/token" (or "<id>/mcp-wiring")
            # as a bank reference and 404.
            ref = path[len("/api/banks/"):]
            wants_token = ref.endswith("/token")
            wants_wiring = ref.endswith("/mcp-wiring")
            if wants_token:
                ref = ref[: -len("/token")]
            elif wants_wiring:
                ref = ref[: -len("/mcp-wiring")]
            bank = find_bank(unquote(ref))
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches", {"ref": ref})
                return
            if wants_token:
                self.json_out(200, {"bank_id": bank["id"], "name": bank["name"],
                                    "token": bank_token(bank["id"])})
                return
            if wants_wiring:
                project_root = _project_root_for_bank(bank)
                if project_root is None:
                    self.json_out(200, {"has_wiring": False, "uses_template": False,
                                        "project_root": None})
                    return
                info = _MCP_WIRING.get(bank["id"], {"has_wiring": False, "uses_template": False})
                self.json_out(200, {"has_wiring": info["has_wiring"],
                                    "uses_template": info["uses_template"],
                                    "project_root": project_root})
                return
            self.json_out(200, bank_info(bank))
            return

        if path == "/api/fs/dirs":
            listing = fs_listing((query.get("path") or [""])[0])
            if listing is None:
                self.fail(400, "bad_request", "нема такої теки (фікстура)",
                          {"path": (query.get("path") or [""])[0]})
                return
            self.json_out(200, listing)
            return

        if path == "/api/status":
            self.json_out(200, {
                "service": {"version": SERVICE_VERSION, "pid": 0,
                            "host": self.server.server_address[0],
                            "port": self.server.server_port,
                            "started_at": datetime.fromtimestamp(
                                STARTED_AT, timezone.utc).astimezone().isoformat(
                                    timespec="seconds"),
                            "uptime_s": round(time.time() - STARTED_AT, 1),
                            "provider": _SETTINGS["provider"],
                            "priority_enabled": True,
                            # `kind` says WHAT was probed: a live resident under
                            # `local`, configuration only under `api`. The page
                            # words the badge from it, so a mock that omitted it
                            # would render an unconfigured endpoint as a dead
                            # process.
                            "embed": {"reachable": True, "host": "127.0.0.1",
                                      "port": 4645,
                                      "kind": _SETTINGS["provider"]}},
                "queue": queue_snapshot(),
                "banks": [bank_info(b) for b in BANKS],
            })
            return

        if path == "/api/tree":
            bank = find_bank((query.get("bank") or [""])[0])
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches",
                          {"ref": (query.get("bank") or [""])[0]})
                return
            self.json_out(200, _tree_response(bank["id"]))
            return

        if path == "/api/file":
            bank = find_bank((query.get("bank") or [""])[0])
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches",
                          {"ref": (query.get("bank") or [""])[0]})
                return
            rel = (query.get("path") or [""])[0]
            if rel.startswith("/") or ".." in rel.split("/"):
                self.fail(400, "path_outside_bank", "path escapes the bank root",
                          {"path": rel})
                return
            record = FILES.get(bank["id"], {}).get(rel)
            if not record:
                self.fail(404, "file_not_found", "no such file in the bank",
                          {"path": rel})
                return
            self.json_out(200, record)
            return

        if path == "/api/logs":
            self.get_logs(query)
            return

        if path == "/api/settings":
            self.json_out(200, settings_payload())
            return

        if path == "/api/autostart":
            self.json_out(200, dict(_AUTOSTART))
            return

        if path == "/api/update/status":
            self.json_out(200, update_status_payload())
            return

        if path == "/api/catalog":
            category = (query.get("category") or [None])[0]
            with _lock:
                entries = [_catalog_entry_info(e) for e in CATALOG
                           if category is None or e["category"] == category]
            self.json_out(200, {"entries": entries})
            return

        if path.startswith("/api/catalog/"):
            entry_id = unquote(path[len("/api/catalog/"):])
            with _lock:
                entry = _find_catalog_entry(entry_id)
            if entry is None:
                self.fail(404, "catalog_entry_not_found",
                          f"no catalog entry with id {entry_id!r}", {"id": entry_id})
                return
            self.json_out(200, _catalog_entry_info(entry))
            return

        if path == "/api/agents":
            with _lock:
                agents = [dict(a) for a in AGENTS]
            self.json_out(200, {"agents": agents})
            return

        if path.startswith("/api/agents/"):
            rest = path[len("/api/agents/"):]
            wants_launch = rest.endswith("/launch")
            if wants_launch:
                rest = rest[: -len("/launch")]
            slug_ref = unquote(rest)
            agent = _find_agent(slug_ref)
            if agent is None:
                self.fail(404, "agent_not_found", "no agent matches", {"slug": slug_ref})
                return
            self.json_out(200, dict(agent["launch"]) if wants_launch else dict(agent))
            return

        self.fail(404, "internal", "no route " + path)

    def get_logs(self, query: dict) -> None:
        kind = (query.get("kind") or [""])[0]
        if kind not in ("query", "index"):
            self.fail(400, "bad_request", "kind must be 'query' or 'index'",
                      {"kind": kind})
            return
        limit = int((query.get("limit") or ["200"])[0])
        offset = int((query.get("offset") or ["0"])[0])

        with _lock:
            rows = list(QUERY_EVENTS if kind == "query" else INDEX_EVENTS)

        ref = (query.get("bank") or [None])[0]
        if ref:
            bank = find_bank(ref)
            if not bank:
                self.fail(404, "bank_not_found", "no bank matches", {"ref": ref})
                return
            rows = [r for r in rows if r["bank_id"] == bank["id"]]

        total = len(rows)
        self.json_out(200, {"kind": kind, "total": total, "limit": limit,
                            "offset": offset, "events": rows[offset:offset + limit]})

    def post_bank(self, body: dict) -> None:
        """`POST /api/banks` — register a fixture bank and pretend to index it."""
        root = (body.get("root") or "").strip().replace("\\", "/").rstrip("/")
        if not root:
            self.fail(400, "bad_request", "root is required", {"root": root})
            return
        if root not in FS_FIXTURE:
            self.fail(400, "root_not_found", "нема такої теки (фікстура)",
                      {"root": root})
            return
        with _lock:
            clash = next((b for b in BANKS if b["root"].rstrip("/") == root), None)
        if clash:
            self.fail(409, "bank_exists", f"уже зареєстровано як {clash['name']!r}",
                      {"root": root})
            return

        name = (body.get("name") or "").strip() or (root.rsplit("/", 1)[-1] or "root")
        bank = {
            "id": hashlib.sha256(root.encode("utf-8")).hexdigest()[:16],
            "name": name,
            "root": root,
            "provider": body.get("provider"),
            "index_provider_key": None,
            "state": "enabled",
            "enabled": True,
            "exists": True,
            "git": False,
            "files": 0,
            "chunks": 0,
            "db_bytes": 0,
            "last_indexed": None,
            # A fresh bank has no index and a bulk task waiting — `empty`, with
            # `queued`, is exactly the distinction decision #11 insists on.
            "status": "empty",
            "queued": 1,
            "indexing": False,
            "last_error": None,
        }
        with _lock:
            BANKS.append(bank)
        broadcast("bank_added", bank["id"], {"bank": dict(bank)})
        threading.Thread(
            target=simulate_task,
            args=(bank,),
            kwargs={"kind": "bulk", "path": None, "batch_ms": Handler.batch_ms},
            daemon=True,
        ).start()
        info = bank_info(bank)
        if body.get("init"):
            info["init"] = _mock_init_result(root)
        self.json_out(201, info)

    def post_agent(self, body: dict) -> None:
        """`POST /api/agents` — mirrors `api_create_agent`'s shape: a name is
        required, an explicit `root` gets `owns_root=False` and a non-empty
        target needs `confirm_adopt` (409 `adoption_confirmation_required`
        otherwise, carrying the same preview the wizard already showed)."""
        name = str(body.get("name") or "").strip()
        if not name:
            self.fail(400, "bad_request", "'name' cannot be empty")
            return
        claude_md = body.get("claude_md")
        confirm_adopt = bool(body.get("confirm_adopt"))
        root_in = body.get("root")

        with _lock:
            slug = _unique_agent_slug(_agent_slugify(name))
            if root_in:
                root = str(root_in).strip().replace("\\", "/").rstrip("/")
                if not root.startswith("/"):
                    self.fail(400, "bad_request", "потрібен абсолютний шлях", {"root": root_in})
                    return
                preview = _agent_preview(root)
                if preview["root_exists"] and not preview["empty"] and not confirm_adopt:
                    self.fail(409, "adoption_confirmation_required",
                              f"{root} is not empty — confirm adoption to proceed",
                              {"root": root_in, "preview": preview})
                    return
                owns_root = False
            else:
                root = f"/home/dev/.mnemo/agents/{slug}"
                owns_root = True

            agent = {
                "slug": slug,
                "name": name,
                "root": root,
                "owns_root": owns_root,
                "created_at": now_iso(),
                "bank_id": f"agent_{slug.replace('-', '_')}",
                "bank_name": name,
                "launch": {"mode": "standard"},
            }
            AGENTS.append(agent)
        _ = claude_md  # fixture has no filesystem to write it to — accepted, not stored
        self.json_out(201, dict(agent))

    def post_catalog_entry(self, body: dict) -> None:
        """`POST /api/catalog` — same validation/dedup rules as `catalog.add`
        (real `src/catalog.py`), against the fixture `CATALOG` list."""
        category = body.get("category")
        if category not in ("mcp", "skill", "rule"):
            self.fail(400, "invalid_catalog_entry",
                      f"unknown category {category!r} — expected one of "
                      f"mcp, skill, rule")
            return
        name = (body.get("name") or "").strip()
        if not name:
            self.fail(400, "invalid_catalog_entry", "name must not be empty")
            return
        content = body.get("content") or ""
        if not content.strip():
            self.fail(400, "invalid_catalog_entry", "content must not be empty")
            return
        if category == "mcp" and _canonical_json(content) is None:
            self.fail(400, "invalid_catalog_entry",
                      "content must be valid JSON for an 'mcp' entry")
            return

        with _lock:
            if category == "mcp":
                dup = _find_duplicate_mcp(content, exclude_id=None)
                if dup is not None:
                    self.fail(409, "catalog_entry_exists",
                              f"an mcp entry with this exact config already "
                              f"exists: {dup['name']!r}",
                              {"existing_id": dup["id"]})
                    return
            entry = {
                "id": f"{category}_{secrets.token_hex(6)}",
                "category": category,
                "name": _unique_catalog_name(name, category),
                "content": content,
                "created_at": now_iso(),
            }
            CATALOG.append(entry)
        self.json_out(201, _catalog_entry_info(entry))

    def patch_catalog_entry(self, entry_id: str, body: dict) -> None:
        with _lock:
            entry = _find_catalog_entry(entry_id)
            if entry is None:
                self.fail(404, "catalog_entry_not_found",
                          f"no catalog entry with id {entry_id!r}", {"id": entry_id})
                return

            fields = {k: v for k, v in body.items() if k in ("name", "content") and v is not None}
            if not fields:
                self.fail(400, "bad_request", "nothing to change", {"id": entry_id})
                return

            new_content = fields.get("content", entry["content"])
            if not new_content.strip():
                self.fail(400, "invalid_catalog_entry", "content must not be empty")
                return
            if entry["category"] == "mcp" and _canonical_json(new_content) is None:
                self.fail(400, "invalid_catalog_entry",
                          "content must be valid JSON for an 'mcp' entry")
                return
            if entry["category"] == "mcp" and "content" in fields:
                dup = _find_duplicate_mcp(new_content, exclude_id=entry_id)
                if dup is not None:
                    self.fail(409, "catalog_entry_exists",
                              f"an mcp entry with this exact config already "
                              f"exists: {dup['name']!r}",
                              {"existing_id": dup["id"]})
                    return

            new_name = entry["name"]
            if "name" in fields:
                new_name = fields["name"].strip()
                if not new_name:
                    self.fail(400, "invalid_catalog_entry", "name cannot be empty")
                    return
                clash = next(
                    (e for e in CATALOG
                     if e["id"] != entry_id and e["category"] == entry["category"]
                     and _norm_catalog_name(e["name"]) == _norm_catalog_name(new_name)),
                    None,
                )
                if clash:
                    self.fail(409, "catalog_entry_exists",
                              f"another {entry['category']} entry is already "
                              f"named {new_name!r}", {"existing_id": clash["id"]})
                    return

            entry["name"] = new_name
            entry["content"] = new_content
        self.json_out(200, _catalog_entry_info(entry))

    def delete_catalog_entry(self, entry_id: str) -> None:
        with _lock:
            entry = _find_catalog_entry(entry_id)
            if entry is None:
                self.fail(404, "catalog_entry_not_found",
                          f"no catalog entry with id {entry_id!r}", {"id": entry_id})
                return
            # `used_by` is always empty here — MN-48 (attach/detach link
            # storage) hasn't landed, same as the real backend's guarded
            # `_catalog_used_by` hook before it exists.
            CATALOG.remove(entry)
        self.json_out(200, {"ok": True})

    def post_reindex(self, body: dict) -> None:
        ref = body.get("bank") or ""
        bank = find_bank(ref)
        if not bank:
            self.fail(404, "bank_not_found", f"no bank matches {ref!r}", {"ref": ref})
            return
        if (bank.get("state") or "enabled") == "disabled":
            self.fail(404, "bank_not_found", "bank is disabled", {"bank": bank["name"]})
            return

        rel = body.get("path")
        full = bool(body.get("full"))
        files = FILES.get(bank["id"], {})
        if rel and rel not in files:
            self.fail(404, "file_not_found", "no such file in the bank", {"path": rel})
            return

        kind = "file" if rel else ("rebuild" if full else "bulk")
        queued = 1 if rel else max(1, len([p for p, r in files.items() if r["indexed"]]))
        task_id = _task_id()

        threading.Thread(
            target=simulate_task,
            args=(bank,),
            kwargs={"kind": kind, "path": rel, "batch_ms": Handler.batch_ms},
            daemon=True,
        ).start()

        self.json_out(202, {"ok": True, "task_ids": [task_id], "queued": queued})

    # -- websocket -------------------------------------------------------

    def handle_ws(self, query: dict) -> None:
        token = (query.get("token") or [""])[0]
        if not NO_AUTH and token != DEV_TOKEN:
            self.fail(401, "unauthorized", "missing or invalid token")
            return
        key = self.headers.get("Sec-WebSocket-Key")
        if not key or "websocket" not in self.headers.get("Upgrade", "").lower():
            self.fail(400, "bad_request", "not a websocket upgrade")
            return

        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
        ).decode("ascii")
        self.wfile.write(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode("ascii") + b"\r\n\r\n"
        )
        self.wfile.flush()

        client = WSClient(self.connection, (query.get("bank") or [None])[0])
        with _clients_lock:
            CLIENTS.append(client)
        self.log_message("ws open (clients=%d)", len(CLIENTS))

        try:
            client.send_json({
                "v": 1, "type": "hello", "ts": now_iso_ms(), "bank_id": None,
                "data": {"version": SERVICE_VERSION,
                         "banks": [b["id"] for b in BANKS],
                         "queue": queue_snapshot()},
            })
            while True:
                frame = read_frame(self.rfile)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    client.send_frame(payload, opcode=0xA)
                # 0x1 text frames are subscribe/pong only — nothing to do here.
        except OSError:
            pass
        finally:
            with _clients_lock:
                if client in CLIENTS:
                    CLIENTS.remove(client)
            self.close_connection = True
            self.log_message("ws close (clients=%d)", len(CLIENTS))


# --------------------------------------------------------------------------
# background noise
# --------------------------------------------------------------------------


def heartbeat() -> None:
    while True:
        time.sleep(30)
        broadcast("ping", None, {})


def query_noise(period: float) -> None:
    """Emit a synthetic `query` WS event so the live log strip can be seen."""
    global _next_query_id
    samples = [("mcp", "як влаштована черга"), ("hook", "порт бекенда"),
               ("cli", "chunk viz"), ("ui", "реіндекс банку")]
    i = 0
    while True:
        time.sleep(period)
        face, text = samples[i % len(samples)]
        i += 1
        bank = BANKS[i % 2]
        row = {"id": _next_query_id, "ts": now_iso(), "bank_id": bank["id"],
               "face": face, "query": text, "path_prefix": None,
               "status": bank["status"], "n_hits": (i % 4), "took_ms": 20.0 + i,
               "hits": []}
        with _lock:
            _next_query_id += 1
            QUERY_EVENTS.insert(0, row)
        broadcast("query", bank["id"], {"face": face, "query": text,
                                        "status": bank["status"],
                                        "n_hits": row["n_hits"],
                                        "took_ms": row["took_ms"]})


def main() -> None:
    parser = argparse.ArgumentParser(description="dev-only mock backend for src/webui")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8919)
    parser.add_argument("--noise", type=float, default=0.0,
                        help="seconds between synthetic query events (0 = off)")
    parser.add_argument("--batch-ms", type=int, default=260,
                        help="simulated time per index batch")
    parser.add_argument("--no-auth", action="store_true",
                        help="skip the token check (real backend is open by "
                             "default too, unless $MNEMO_API_TOKEN/require_login "
                             "is set) - this mock otherwise always requires one")
    args = parser.parse_args()

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit("dev server binds loopback only")

    Handler.batch_ms = args.batch_ms
    global NO_AUTH
    NO_AUTH = args.no_auth

    threading.Thread(target=heartbeat, daemon=True).start()
    threading.Thread(target=_auto_pending_scheduler, daemon=True).start()
    if args.noise > 0:
        threading.Thread(target=query_noise, args=(args.noise,), daemon=True).start()

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"mock backend on http://{args.host}:{args.port}", flush=True)
    if args.no_auth:
        print(f"open  http://{args.host}:{args.port}/ui/  (no token required)", flush=True)
    else:
        print(f"open  http://{args.host}:{args.port}/ui/?token={DEV_TOKEN}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
