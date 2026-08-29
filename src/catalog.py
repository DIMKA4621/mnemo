"""Catalog — the general MCP/Skills/Rules registry (MN-41).

Agent-agnostic, on purpose: this is the workspace's package-manager cache —
one flat store of reusable entries a human adds by hand (name + config/text),
independent of any agent. Attaching an entry to a specific agent's
``.mcp.json`` / ``.claude/skills`` / ``.claude/rules`` is MN-42's concern, not
this module's — this module never imports ``agent_registry``, and answers
only "does anything claim to use this id" through a guarded hook the API
layer wires in (``api.py``'s ``_catalog_used_by``, same guarded-import shape
as ``api._queue`` uses for ``workqueue`` ahead of phase 3).

One JSON file, ``STATE_DIR/catalog.json`` — the same lock / atomic-write /
cache-by-mtime contract as ``registry.py``'s ``banks.json`` and
``agent_registry.py``'s ``agents.json``. The load/save/`_file_lock` machinery
below is copied rather than imported, same reasoning as ``agent_registry.py``
gives for doing the same: those helpers are module-private in ``registry.py``
by design (one file, one owner).

Three properties this module enforces:

* **``id`` is random** (``{category}_{token_hex(6)}``), never derived from
  content — unlike ``Bank.id``, an entry's content changes in place on every
  edit, and a content-derived id would move under a caller mid-edit.
* **``category`` is fixed at creation.** Changing it after the fact would
  upend both the JSON/dedup rules below (``mcp``-only) and the id's own
  prefix.
* **``name`` is unique within its category**, case-insensitive, auto-suffixed
  on collision on create — same ``unique_slug``/``unique_name`` shape as the
  other two registries. Without it MN-42's attach picker would show
  indistinguishable rows. An explicit rename that collides is refused rather
  than silently suffixed, same call as ``registry.update``'s rename guard.

Validation is strictly stronger than the frontend mockup it mirrors
(``.claude/scratch/agents-page-mockup/app.js``):

* ``content`` must be non-empty for all three categories (the mockup never
  enforced this for skills/rules).
* ``mcp`` content must be valid JSON; ``skill``/``rule`` content is free text
  — no JSON validation, no dedup, same as the mockup.
* ``mcp`` dedup compares **canonicalised** JSON
  (``json.dumps(parsed, sort_keys=True)``), not the mockup's
  ``JSON.stringify(JSON.parse(text))``, which compares by *original* key
  order and misses two configs that differ only in key order.
* ``{{VAR}}`` placeholders are parsed with the same regex shape as the
  mockup's ``mcpParseVars`` (``mcp`` entries only), but always **recomputed
  here** from ``content`` — a client-supplied list, if one were sent, is
  never trusted as data.
"""
from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("mnemo.catalog")

CATALOG_VERSION = 1

CATEGORY_MCP = "mcp"
CATEGORY_SKILL = "skill"
CATEGORY_RULE = "rule"
CATEGORIES = (CATEGORY_MCP, CATEGORY_SKILL, CATEGORY_RULE)

# Fields this module owns. Anything else found in an entry object is
# preserved verbatim under `CatalogEntry.extra` and written back on save —
# same round-trip contract as `registry.Bank.extra` / `agent_registry.Agent.extra`.
_KNOWN_FIELDS = frozenset({"id", "category", "name", "content", "created_at"})


class EntryNotFound(LookupError):
    """No catalog entry matches the given id."""


class InvalidCatalogEntry(ValueError):
    """``content`` fails validation for its category: empty, or invalid JSON
    for an ``mcp`` entry."""


class EntryExists(ValueError):
    """Either an explicit rename collides with another entry in the same
    category, or — for ``mcp`` — another entry already carries this exact
    canonicalised config.

    ``existing_id`` is set when there is one specific entry to point at (both
    cases qualify), so the API layer can surface it in the error detail.
    """

    def __init__(self, message: str, *, existing_id: str | None = None) -> None:
        super().__init__(message)
        self.existing_id = existing_id


# --------------------------------------------------------------- settings


def state_dir() -> Path:
    """The writable state directory, looked up through the module — never a
    frozen ``from .config import STATE_DIR`` binding (see
    ``registry.state_dir`` for why that matters)."""
    return Path(config.STATE_DIR)


def catalog_file() -> Path:
    """Location of the catalog document, resolved per call (see `state_dir`)."""
    return state_dir() / "catalog.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _norm_name(name: str) -> str:
    """Names are addresses within a category: compared stripped and
    case-insensitively, same as `registry._norm_name`."""
    return name.strip().casefold()


# ---------------------------------------------------------------- vars / JSON

# Same shape as the frontend mockup's `mcpParseVars` regex — kept identical so
# a config that shows N chips in the console parses to the same N names here.
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def parse_vars(content: str) -> list[str]:
    """``{{VAR}}`` placeholder names in ``content``, first-seen order,
    deduplicated. The only copy of this logic anything may trust — the API
    layer recomputes it from ``content`` on every create/update rather than
    accepting a client-supplied list (see the module docstring)."""
    seen: dict[str, None] = {}
    for m in _VAR_RE.finditer(content or ""):
        seen.setdefault(m.group(1), None)
    return list(seen)


def canonical_json(content: str) -> str | None:
    """``content`` re-serialised with sorted keys, or ``None`` if it is not
    valid JSON.

    Stronger than the mockup's dedup check
    (``JSON.stringify(JSON.parse(text))``), which compares two configs by
    their *original* key order and misses a config that differs from one
    already in the catalog only in key order.

    ``json.loads`` recurses one Python stack frame per nesting level, so a
    deeply nested array/object (``"["*20000 + "]"*20000`` reproduces it)
    raises ``RecursionError`` instead of ``json.JSONDecodeError`` — caught
    here for the same reason a malformed document is: this is untrusted
    ``content`` failing to parse as JSON, not a bug in this process, and it
    must come back as the 400 ``invalid_catalog_entry`` callers already
    handle rather than falling through to the global 500 handler.
    """
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError, RecursionError):
        return None
    return json.dumps(parsed, sort_keys=True, ensure_ascii=False)


def new_id(category: str) -> str:
    return f"{category}_{secrets.token_hex(6)}"


def unique_name(
    candidate: str,
    category: str,
    *,
    entries: list[CatalogEntry] | None = None,
    exclude_id: str | None = None,
) -> str:
    """``candidate`` if free within ``category``, else ``candidate-2``,
    ``candidate-3``, … Case-insensitive, same shape as `registry.unique_name`."""
    pool = load() if entries is None else entries
    taken = {
        _norm_name(e.name)
        for e in pool
        if e.category == category and (exclude_id is None or e.id != exclude_id)
    }
    base = candidate.strip() or category
    if _norm_name(base) not in taken:
        return base
    n = 2
    while _norm_name(f"{base}-{n}") in taken:
        n += 1
    return f"{base}-{n}"


# ------------------------------------------------------------------- model


@dataclass(frozen=True)
class CatalogEntry:
    """One registered catalog entry."""

    id: str
    category: str
    name: str
    content: str
    created_at: str = ""
    # Fields a human (or a future version) put in catalog.json that we do not
    # understand. Round-tripped untouched, same as `registry.Bank.extra`.
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def vars(self) -> list[str]:
        """`{{VAR}}` placeholders found in `content` — `mcp` entries only
        (the mockup never parses vars for skills/rules), computed fresh from
        `content` every time and never stored."""
        return parse_vars(self.content) if self.category == CATEGORY_MCP else []

    def to_json(self) -> dict[str, Any]:
        """Serialised form — unknown fields first so ours always read last."""
        data: dict[str, Any] = dict(self.extra)
        data.update(
            {
                "id": self.id,
                "category": self.category,
                "name": self.name,
                "content": self.content,
                "created_at": self.created_at,
            }
        )
        return data


def _from_json(obj: dict[str, Any]) -> CatalogEntry:
    return CatalogEntry(
        id=str(obj["id"]),
        category=str(obj.get("category") or ""),
        name=str(obj["name"]),
        content=str(obj.get("content") or ""),
        created_at=str(obj.get("created_at") or ""),
        extra={k: v for k, v in obj.items() if k not in _KNOWN_FIELDS},
    )


# -------------------------------------------------------------- load / save
#
# Same reasoning and the same shape as `registry.py`'s load/save/`_file_lock`:
# a cross-process lock file around every read-modify-write, an in-process
# cache invalidated by the document's (mtime, size) signature, and an atomic
# tmp + `os.replace` on write.

_lock = threading.RLock()
_cache: list[CatalogEntry] | None = None
_cache_sig: tuple[str, int | None, int | None] | None = None
_doc_extra: dict[str, Any] = {}

_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 60.0


@contextlib.contextmanager
def _file_lock():
    """Exclusive lock around a read-modify-write of catalog.json."""
    path = catalog_file().with_name(catalog_file().name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    handle = None
    while True:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > _LOCK_STALE_S:
                    log.warning("breaking stale catalog lock %s", path)
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"catalog is locked by another process ({path})")
            time.sleep(0.05)
        except OSError as exc:
            if exc.errno == errno.EACCES:      # transient on Windows
                time.sleep(0.05)
                continue
            raise
    try:
        with contextlib.suppress(OSError):
            os.write(handle, str(os.getpid()).encode())
        yield
    finally:
        with contextlib.suppress(OSError):
            os.close(handle)
        with contextlib.suppress(OSError):
            path.unlink()


def _signature(path: Path) -> tuple[str, int | None, int | None]:
    try:
        st = path.stat()
    except FileNotFoundError:
        return (str(path), None, None)
    return (str(path), st.st_mtime_ns, st.st_size)


def _read(path: Path) -> tuple[list[CatalogEntry], dict[str, Any]]:
    if not path.exists():
        return [], {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("entries"), list):
        raise ValueError(f"{path}: expected an object with an 'entries' array")
    entries = [_from_json(o) for o in doc["entries"] if isinstance(o, dict)]
    extra = {k: v for k, v in doc.items() if k not in ("version", "entries")}
    return entries, extra


def load(*, force: bool = False) -> list[CatalogEntry]:
    """All catalog entries, reloading when catalog.json changed on disk."""
    global _cache, _cache_sig, _doc_extra
    with _lock:
        path = catalog_file()
        sig = _signature(path)
        if not force and _cache is not None and _cache_sig == sig:
            return list(_cache)
        entries, extra = _read(path)
        _cache, _cache_sig, _doc_extra = entries, sig, extra
        return list(entries)


def save(entries: list[CatalogEntry]) -> None:
    """Write the catalog atomically (tmp + ``os.replace``)."""
    global _cache, _cache_sig
    with _lock:
        path = catalog_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        doc: dict[str, Any] = dict(_doc_extra)
        doc["version"] = CATALOG_VERSION
        doc["entries"] = [e.to_json() for e in entries]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # An `mcp` entry's content can carry a secret (an env block with an
        # API key), so this file gets the same treatment as banks.json /
        # api.token: readable by the owner only. POSIX-only in effect — see
        # `registry.save`'s note on what this buys on Windows.
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        _cache = list(entries)
        _cache_sig = _signature(path)


# ------------------------------------------------------------------ lookup


def list_entries(category: str | None = None) -> list[CatalogEntry]:
    entries = load()
    if category is None:
        return entries
    return [e for e in entries if e.category == category]


def get(entry_id: str) -> CatalogEntry:
    for entry in load():
        if entry.id == entry_id:
            return entry
    raise EntryNotFound(f"no catalog entry with id {entry_id!r}")


# ------------------------------------------------------------------ mutate


def _validate_category(category: str) -> None:
    if category not in CATEGORIES:
        raise InvalidCatalogEntry(
            f"unknown category {category!r} — expected one of {CATEGORIES}"
        )


def _validate_content(category: str, content: str) -> None:
    if not (content or "").strip():
        raise InvalidCatalogEntry("content must not be empty")
    if category == CATEGORY_MCP and canonical_json(content) is None:
        raise InvalidCatalogEntry("content must be valid JSON for an 'mcp' entry")


def _find_duplicate_mcp(
    content: str, entries: list[CatalogEntry], *, exclude_id: str | None
) -> CatalogEntry | None:
    """The existing `mcp` entry whose canonicalised config matches
    `content`'s, or `None`. Only ever meaningful for `mcp` — callers guard
    that themselves, this just returns `None` when `content` is not valid
    JSON."""
    normalized = canonical_json(content)
    if normalized is None:
        return None
    for entry in entries:
        if entry.category != CATEGORY_MCP:
            continue
        if exclude_id is not None and entry.id == exclude_id:
            continue
        if canonical_json(entry.content) == normalized:
            return entry
    return None


def add(category: str, name: str, content: str) -> CatalogEntry:
    """Register a new catalog entry. Returns the stored entry — read its
    ``name`` back, it may carry a uniqueness suffix."""
    _validate_category(category)
    clean_name = (name or "").strip()
    if not clean_name:
        raise InvalidCatalogEntry("name must not be empty")
    _validate_content(category, content)

    with _lock, _file_lock():
        # Re-read inside the lock: another process may have added an entry
        # since our cached copy, and appending to a stale list would drop it
        # (same reasoning as `registry.add`).
        entries = load(force=True)
        if category == CATEGORY_MCP:
            dup = _find_duplicate_mcp(content, entries, exclude_id=None)
            if dup is not None:
                raise EntryExists(
                    f"an mcp entry with this exact config already exists: "
                    f"{dup.name!r}",
                    existing_id=dup.id,
                )
        chosen = unique_name(clean_name, category, entries=entries)
        entry = CatalogEntry(
            id=new_id(category),
            category=category,
            name=chosen,
            content=content,
            created_at=_now_iso(),
        )
        entries.append(entry)
        save(entries)
        return entry


def update(
    entry_id: str, *, name: str | None = None, content: str | None = None
) -> CatalogEntry:
    """Edit a catalog entry in place — the same validation and dedup rules as
    `add`, re-run against the edited content. ``category`` is not updatable
    (see the module docstring)."""
    with _lock, _file_lock():
        entries = load(force=True)
        idx = next((i for i, e in enumerate(entries) if e.id == entry_id), None)
        if idx is None:
            raise EntryNotFound(f"no catalog entry with id {entry_id!r}")
        current = entries[idx]

        new_content = current.content if content is None else content
        _validate_content(current.category, new_content)
        if current.category == CATEGORY_MCP and content is not None:
            dup = _find_duplicate_mcp(new_content, entries, exclude_id=entry_id)
            if dup is not None:
                raise EntryExists(
                    f"an mcp entry with this exact config already exists: "
                    f"{dup.name!r}",
                    existing_id=dup.id,
                )

        new_name = current.name
        if name is not None:
            new_name = name.strip()
            if not new_name:
                raise InvalidCatalogEntry("name cannot be empty")
            clash = [
                e
                for e in entries
                if e.id != entry_id
                and e.category == current.category
                and _norm_name(e.name) == _norm_name(new_name)
            ]
            if clash:
                # Deliberately not auto-suffixed: a rename is an intentional
                # act, same call as `registry.update`'s rename guard.
                raise EntryExists(
                    f"another {current.category} entry is already named "
                    f"{new_name!r}",
                    existing_id=clash[0].id,
                )

        updated = CatalogEntry(
            id=current.id,
            category=current.category,
            name=new_name,
            content=new_content,
            created_at=current.created_at,
            extra=current.extra,
        )
        entries[idx] = updated
        save(entries)
        return updated


def remove(entry_id: str) -> None:
    """Unregister a catalog entry. Whether it is safe to remove — i.e.
    whether any agent still references it — is decided by the caller
    (`api.py`, via the guarded ``used_by`` hook) before this is ever called;
    this function only performs the removal."""
    with _lock, _file_lock():
        entries = load(force=True)
        keep = [e for e in entries if e.id != entry_id]
        if len(keep) == len(entries):
            raise EntryNotFound(f"no catalog entry with id {entry_id!r}")
        save(keep)
