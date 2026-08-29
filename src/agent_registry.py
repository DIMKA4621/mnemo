"""Agent registry — the list of agent folders this machine's service knows.

One JSON file, ``STATE_DIR/agents.json`` — same shape and same concurrency
story as ``registry.py``'s ``banks.json``: hand-editable, re-read whenever its
mtime changes, written atomically (tmp + ``os.replace``) under a cross-process
lock file so the CLI, the service and a human with an editor never tear it.

An agent is a folder (``Agents-design.md`` §1/§2):

```
<root>/
  CLAUDE.md
  launch.json        per-agent launch config (mode: standard | custom)
  .claude/rules/
  .claude/skills/
  .mcp.json
  memory/            registered as an ordinary bank — same registry.add()
                      call the console's "add bank" dialog makes
```

Two properties this module enforces, mirroring ``registry.Bank``:

* **``bank_id`` is derived, never stored** — a pure function of
  ``root / "memory"`` via ``config.bank_id``, same reasoning as ``Bank.id``:
  storing it would be a second thing that can disagree with the folder it is
  supposed to describe.
* **``owns_root`` is the one field that changes what ``delete()`` may touch,
  and it is granted for exactly one case: ``create()`` with no explicit
  ``root``**, i.e. the folder is ``config.AGENTS_DIR / slug`` and nothing
  else could have put anything there first. Every other path —
  ``create()``/``adopt()`` with an explicit ``root``, empty or not — gets
  ``owns_root=False``. ``delete()`` re-derives that one expected path and
  checks it against the stored ``root`` with **exact equality** before ever
  calling ``rmtree``, rather than trusting the stored boolean or a looser
  "somewhere under AGENTS_DIR" check — ``agents.json`` is hand-editable, so a
  tampered or corrupted entry claiming ownership of some *other* folder must
  fail this check, not walk it. (Narrowed 2026-08-29 after an independent
  review reproduced exactly that as a live data-loss bug against the
  original wider rule — see MN-40's review comment.) For every
  ``owns_root=False`` agent, ``delete()`` only ever drops the registry
  entries (this module's own, and the memory bank); the folder and every
  file a user already had in it is left untouched. This is the one
  genuinely destructive operation in this module.
"""
from __future__ import annotations

import contextlib
import errno
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import catalog, config, registry

log = logging.getLogger("mnemo.agent_registry")

AGENTS_VERSION = 1

# Fields this module owns. Anything else found in an agent object is
# preserved verbatim under `Agent.extra` and written back on save — same
# round-trip contract as `registry.Bank.extra`.
_KNOWN_FIELDS = frozenset({"slug", "name", "root", "owns_root", "created_at"})


class AgentNotFound(LookupError):
    """No registered agent matches the given slug."""


class AgentExists(ValueError):
    """The target folder already exists and is not empty."""


class InvalidLinksConfig(ValueError):
    """``links.json`` is malformed, or fails structural validation."""


class LinkNotFound(LookupError):
    """No link matches the given (agent, category, entry_id)."""


class LinkExists(ValueError):
    """This catalog entry is already attached in this (agent, category)."""


class LinkNameExists(ValueError):
    """Another link in this (agent, category) already carries this name.

    ``existing_entry_id`` names the entry already holding it, so the API
    layer can surface it in the error detail — same shape as
    ``catalog.EntryExists``'s ``existing_id``.
    """

    def __init__(self, message: str, *, existing_entry_id: str | None = None) -> None:
        super().__init__(message)
        self.existing_entry_id = existing_entry_id


class CategoryMismatch(ValueError):
    """The catalog entry's own category does not match the URL category."""


class UnknownLinkVar(ValueError):
    """``vars`` names a key the catalog entry does not currently declare."""


class InvalidSubstitutedConfig(ValueError):
    """An ``mcp`` entry's content, after ``{{VAR}}`` substitution, is not
    valid JSON. Nothing is written when this is raised."""


class InvalidLinkName(ValueError):
    """``name`` is empty, or would be unsafe to use as a filesystem path
    component (``skill``/``rule`` categories only — an ``mcp`` link's name is
    only ever a JSON object key, never a path)."""


class LinkPathConflict(ValueError):
    """The on-disk materialization target already exists and is not owned by
    any link this agent's ``links.json`` currently tracks — most likely a
    manual edit to ``.mcp.json``/``.claude/skills``/``.claude/rules``.
    Refuse rather than silently overwrite it."""


# --------------------------------------------------------------- settings


def state_dir() -> Path:
    """The writable state directory, looked up through the module — never a
    frozen ``from .config import STATE_DIR`` binding (see `registry.state_dir`
    for why that matters: a later repoint of `config.STATE_DIR` must be seen
    by every call here, not just the ones made before it changed)."""
    return Path(config.STATE_DIR)


def agents_file() -> Path:
    """Location of the agent index, resolved per call (see `state_dir`)."""
    return state_dir() / "agents.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _norm_slug(slug: str) -> str:
    return slug.strip().casefold()


_SLUG_RUN = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase, non-alphanumeric runs collapsed to ``-``, edges trimmed.

    A name that yields nothing usable (all-Cyrillic, all-punctuation) falls
    back to the literal ``"agent"`` and lets `unique_slug` disambiguate
    (``agent``, ``agent-2``, ...) rather than inventing a transliteration.
    """
    lowered = (name or "").strip().lower()
    slug = _SLUG_RUN.sub("-", lowered).strip("-")
    return slug or "agent"


def unique_slug(base: str, existing: Iterable[str]) -> str:
    """``base`` if free, else ``base-2``, ``base-3``, ... Case-insensitive,
    same shape as `registry.unique_name`."""
    taken = {_norm_slug(s) for s in existing}
    candidate = base.strip() or "agent"
    if _norm_slug(candidate) not in taken:
        return candidate
    n = 2
    while _norm_slug(f"{candidate}-{n}") in taken:
        n += 1
    return f"{candidate}-{n}"


# ------------------------------------------------------------------- model


@dataclass(frozen=True)
class Agent:
    """One registered agent folder."""

    slug: str
    name: str
    root: Path
    owns_root: bool
    created_at: str = ""
    # Fields a human (or a future version) put in agents.json that we do not
    # understand. Round-tripped untouched, same as `registry.Bank.extra`.
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def memory_root(self) -> Path:
        return self.root / "memory"

    @property
    def bank_id(self) -> str:
        """This agent's memory bank id — a pure function of its root, never
        stored (see the module docstring)."""
        return config.bank_id(self.memory_root)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = dict(self.extra)
        data.update(
            {
                "slug": self.slug,
                "name": self.name,
                "root": self.root.as_posix(),
                "owns_root": self.owns_root,
                "created_at": self.created_at,
            }
        )
        return data


def _from_json(obj: dict[str, Any]) -> Agent:
    return Agent(
        slug=str(obj["slug"]),
        name=str(obj["name"]),
        root=Path(str(obj["root"])).expanduser(),
        owns_root=bool(obj.get("owns_root", False)),
        created_at=str(obj.get("created_at") or ""),
        extra={k: v for k, v in obj.items() if k not in _KNOWN_FIELDS},
    )


# -------------------------------------------------------------- load / save
#
# Same reasoning and the same shape as `registry.py`'s load/save/`_file_lock`:
# a cross-process lock file around every read-modify-write, an in-process
# cache invalidated by the document's (mtime, size) signature, and an atomic
# tmp + `os.replace` on write. Copied rather than imported — those helpers are
# module-private in `registry.py` by design (one file, one owner).

_lock = threading.RLock()
_cache: list[Agent] | None = None
_cache_sig: tuple[str, int | None, int | None] | None = None
_doc_extra: dict[str, Any] = {}

_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 60.0


@contextlib.contextmanager
def _file_lock():
    """Exclusive lock around a read-modify-write of agents.json."""
    path = agents_file().with_name(agents_file().name + ".lock")
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
                    log.warning("breaking stale agent-registry lock %s", path)
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"agent registry is locked by another process ({path})")
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


def _read(path: Path) -> tuple[list[Agent], dict[str, Any]]:
    if not path.exists():
        return [], {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("agents"), list):
        raise ValueError(f"{path}: expected an object with an 'agents' array")
    agents = [_from_json(o) for o in doc["agents"] if isinstance(o, dict)]
    extra = {k: v for k, v in doc.items() if k not in ("version", "agents")}
    return agents, extra


def load(*, force: bool = False) -> list[Agent]:
    """All registered agents, reloading when agents.json changed on disk."""
    global _cache, _cache_sig, _doc_extra
    with _lock:
        path = agents_file()
        sig = _signature(path)
        if not force and _cache is not None and _cache_sig == sig:
            return list(_cache)
        agents, extra = _read(path)
        _cache, _cache_sig, _doc_extra = agents, sig, extra
        return list(agents)


def save(agents: list[Agent]) -> None:
    """Write the index atomically (tmp + ``os.replace``)."""
    global _cache, _cache_sig
    with _lock:
        path = agents_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        doc: dict[str, Any] = dict(_doc_extra)
        doc["version"] = AGENTS_VERSION
        doc["agents"] = [a.to_json() for a in agents]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        _cache = list(agents)
        _cache_sig = _signature(path)


# ------------------------------------------------------------------ lookup


def list_agents() -> list[Agent]:
    return load()


def get(slug: str) -> Agent:
    wanted = _norm_slug(slug)
    for agent in load():
        if _norm_slug(agent.slug) == wanted:
            return agent
    raise AgentNotFound(f"no agent with slug {slug!r}")


def rename(slug: str, new_name: str) -> Agent:
    """Change only ``Agent.name`` — ``slug`` and ``root`` never move.

    Unlike ``registry.update``'s bank rename, this has no cross-entry
    uniqueness check: ``Agent.name`` was never unique to begin with
    (`create`/`adopt` never enforce it either, only `slug` is), so a rename
    is not the place to start.
    """
    clean = (new_name or "").strip()
    if not clean:
        raise ValueError("name must not be empty")
    with _lock, _file_lock():
        agents = load(force=True)
        idx = next(
            (i for i, a in enumerate(agents) if _norm_slug(a.slug) == _norm_slug(slug)),
            None,
        )
        if idx is None:
            raise AgentNotFound(f"no agent with slug {slug!r}")
        current = agents[idx]
        updated = Agent(
            slug=current.slug,
            name=clean,
            root=current.root,
            owns_root=current.owns_root,
            created_at=current.created_at,
            extra=current.extra,
        )
        agents[idx] = updated
        save(agents)
        return updated


# --------------------------------------------------------------- launch.json


class InvalidLaunchConfig(ValueError):
    """``launch.json`` is malformed, or fails validation."""


_LAUNCH_MODES = frozenset({"standard", "custom"})
_CUSTOM_FIELDS = frozenset({"mode", "host", "port", "model", "autocompact", "extra_args"})


def validate_launch_config(data: Any) -> dict:
    """Normalise and validate a launch-config document.

    Storage-layer validation only — this treats the document as an opaque,
    structural dict and interprets nothing semantically (the systemic/proxy
    axis for ``mode: custom`` is still open, see the Jira decision log; that
    is a future ``agent_runtime.py`` concern, not this module's).

    ``mode: "standard"`` takes no other fields. ``mode: "custom"`` requires a
    non-empty ``host`` string and a ``port`` in 1..65535; ``model`` (string or
    null), ``autocompact`` (number or null — its unit is deliberately left
    undefined) and ``extra_args`` (a list of strings) are optional.
    """
    if not isinstance(data, dict):
        raise InvalidLaunchConfig("launch.json must be a JSON object")

    mode = data.get("mode")
    if mode not in _LAUNCH_MODES:
        raise InvalidLaunchConfig(
            f"'mode' must be one of {sorted(_LAUNCH_MODES)}, got {mode!r}"
        )

    if mode == "standard":
        extra = set(data) - {"mode"}
        if extra:
            raise InvalidLaunchConfig(
                f"mode 'standard' takes no other fields, got {sorted(extra)}"
            )
        return {"mode": "standard"}

    # mode == "custom"
    unknown = set(data) - _CUSTOM_FIELDS
    if unknown:
        raise InvalidLaunchConfig(
            f"unknown field(s) for mode 'custom': {sorted(unknown)}"
        )

    host = data.get("host")
    if not isinstance(host, str) or not host.strip():
        raise InvalidLaunchConfig("mode 'custom' requires a non-empty 'host' string")

    port = data.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        raise InvalidLaunchConfig(
            "mode 'custom' requires 'port' to be an integer in 1..65535"
        )

    result: dict[str, Any] = {"mode": "custom", "host": host, "port": port}

    if "model" in data:
        model = data["model"]
        if model is not None and not isinstance(model, str):
            raise InvalidLaunchConfig("'model' must be a string or null")
        result["model"] = model

    if "autocompact" in data:
        autocompact = data["autocompact"]
        valid = autocompact is None or (
            isinstance(autocompact, (int, float)) and not isinstance(autocompact, bool)
        )
        if not valid:
            raise InvalidLaunchConfig("'autocompact' must be a number or null")
        result["autocompact"] = autocompact

    if "extra_args" in data:
        extra_args = data["extra_args"]
        if not isinstance(extra_args, list) or not all(
            isinstance(a, str) for a in extra_args
        ):
            raise InvalidLaunchConfig("'extra_args' must be a list of strings")
        result["extra_args"] = list(extra_args)

    return result


def _launch_path(root: Path) -> Path:
    return Path(root) / "launch.json"


def read_launch_config(root: Path) -> dict:
    """The agent's launch config. Missing file -> default ``{"mode":
    "standard"}``, never an error — an agent created before this file existed
    (or one whose file was deleted by hand) is not broken, it is standard."""
    path = _launch_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"mode": "standard"}
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidLaunchConfig(f"{path} is not valid JSON: {exc}") from exc
    return validate_launch_config(data)


def write_launch_config(root: Path, data: dict) -> dict:
    """Validate, then write atomically (tmp + ``os.replace``). Returns the
    normalised document that was written."""
    validated = validate_launch_config(data)
    path = _launch_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(validated, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return validated


# ---------------------------------------------------------------- CLAUDE.md


def _claude_md_path(root: Path) -> Path:
    return Path(root) / "CLAUDE.md"


def read_claude_md(root: Path) -> str:
    """The agent's ``CLAUDE.md``. Missing file -> ``""``, never an error —
    same "absence is not broken" stance as `read_launch_config`."""
    try:
        return _claude_md_path(root).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_claude_md(root: Path, text: str) -> None:
    """Write atomically (tmp + ``os.replace``). No JSON validation — this is
    plain text, unlike ``launch.json``/``links.json``."""
    path = _claude_md_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text or "", encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------- links.json
#
# The agent <-> catalog attachment record — deliberately its own file,
# separate from ``agents.json`` (the registry) and ``launch.json`` (how to
# start the agent): one responsibility, one file, same as the rest of this
# module. Same "light" pattern as ``launch.json``/``CLAUDE.md`` — no
# cross-process ``_file_lock()``, this is a single agent's own file, not a
# shared registry several processes race to edit — but still validated on
# read the way `launch.json` is, since it is just as hand-editable.
#
# `vars` here is a NAME -> VALUE mapping (what to substitute a catalog
# entry's `{{VAR}}` placeholders with for THIS attachment). That is the
# opposite shape from `catalog.CatalogEntry.vars`, which is a *list* of the
# placeholder *names* an entry declares — deliberately asymmetric: the
# catalog knows which vars an entry needs, an agent's link knows what to fill
# them in with. Easy to confuse the two; do not.


LINKS_VERSION = 1


def _links_path(root: Path) -> Path:
    return Path(root) / "links.json"


def _default_links_doc() -> dict:
    return {"version": LINKS_VERSION, "mcp": [], "skill": [], "rule": []}


def _normalize_links_doc(data: Any) -> dict:
    if not isinstance(data, dict):
        raise InvalidLinksConfig("links.json must be a JSON object")
    doc: dict[str, Any] = {"version": LINKS_VERSION}
    for category in catalog.CATEGORIES:
        raw = data.get(category, [])
        if not isinstance(raw, list):
            raise InvalidLinksConfig(f"{category!r} must be an array")
        bucket: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise InvalidLinksConfig(f"a {category!r} entry must be an object")
            entry_id = item.get("entry_id")
            name = item.get("name")
            link_vars = item.get("vars", {})
            if not isinstance(entry_id, str) or not entry_id:
                raise InvalidLinksConfig(f"a {category!r} entry is missing 'entry_id'")
            if not isinstance(name, str) or not name:
                raise InvalidLinksConfig(f"a {category!r} entry is missing 'name'")
            if not isinstance(link_vars, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in link_vars.items()
            ):
                raise InvalidLinksConfig(
                    f"a {category!r} entry's 'vars' must be an object of string -> string"
                )
            bucket.append({"entry_id": entry_id, "name": name, "vars": dict(link_vars)})
        doc[category] = bucket
    return doc


def read_links_config(root: Path) -> dict:
    """This agent's link record. Missing file -> the empty document, never
    an error — same "absence is not broken" stance as `read_launch_config`."""
    path = _links_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_links_doc()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidLinksConfig(f"{path} is not valid JSON: {exc}") from exc
    return _normalize_links_doc(data)


def write_links_config(root: Path, data: dict) -> dict:
    """Validate, then write atomically (tmp + ``os.replace``). Returns the
    normalised document that was written."""
    normalized = _normalize_links_doc(data)
    path = _links_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return normalized


# --------------------------------------------------- links.json: materialization
#
# Write-through, at attach/edit/detach time — never at read time. Claude
# Code's own CLI reads `.mcp.json` / `.claude/skills/` / `.claude/rules/`
# when an agent starts; mnemo does not intercept that read, so the only way
# a catalog entry actually reaches the agent is to write these files
# ourselves, right now, in the same call that records the link.

_VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")  # mirrors catalog._VAR_RE


def _substitute_vars(content: str, values: dict[str, str]) -> str:
    """``{{VAR}}`` -> ``values[VAR]`` for every name `values` supplies; a
    placeholder with no supplied value is left as literal text."""
    return _VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), content or "")


def _parse_substituted_mcp_config(content: str) -> Any:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidSubstitutedConfig(
            f"substituted config is not valid JSON: {exc}"
        ) from exc


_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')

_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


def _validate_link_name_path_safe(root: Path, category: str, local_name: str) -> None:
    """``local_name`` becomes a real filesystem path component under this
    agent's ``.claude/skills/`` or ``.claude/rules/`` — validate it the way
    a filesystem path deserves, not just against a couple of patterns an
    attacker might type.

    Three independent checks, each catching a different shape of bad input:

    * **Containment**, after actually resolving the candidate path — this is
      the one that matters. A bare separator/``..`` denylist misses a
      Windows drive-letter-anchored name (``"C:mnemo-poc"``): ``pathlib``'s
      ``/`` treats a component starting with ``X:`` as a NEW anchor and
      silently discards everything joined before it, so ``Path(root) /
      ".claude" / "skills" / "C:mnemo-poc"`` resolves to ``C:\\mnemo-poc``
      — nowhere near ``root`` (confirmed live: this actually wrote a
      SKILL.md to the drive root before this check existed). Resolving the
      real candidate and checking it stays under ``root`` closes this whole
      class of "anchor" tricks in one place, instead of special-casing
      individual patterns. The separator/``.``/``..`` checks stay too — not
      as the security boundary any more, but because a name silently
      spanning multiple path segments (``"a/b"``) is confusing even when it
      would have stayed under ``root``.
    * A **denylist of characters NTFS itself rejects** (``<>:"|?*``) —
      without this, ``mkdir``/``write_text`` raises a raw ``OSError`` that
      would otherwise surface as an undifferentiated 500, not a clean 400.
    * **Windows reserved device names** (``con``, ``nul``, ``prn``, ``aux``,
      ``com1``..``9``, ``lpt1``..``9``), checked against the name's stem
      before its FIRST dot — ``"nul.md"`` is exactly as reserved as
      ``"nul"`` on Windows, and without this check ``mkdir("nul")``
      sometimes silently succeeds (observed: it really does create a
      directory) and sometimes doesn't, which is worse than a clean
      rejection either way.
    """
    if not local_name or "/" in local_name or "\\" in local_name or local_name in (".", ".."):
        raise InvalidLinkName(
            f"name {local_name!r} cannot be used as a file/folder name "
            f"(no path separators, and not '.' or '..')"
        )
    bad_chars = set(local_name) & _WINDOWS_INVALID_CHARS
    if bad_chars:
        raise InvalidLinkName(
            f"name {local_name!r} contains character(s) not valid in a "
            f"file/folder name: {sorted(bad_chars)!r}"
        )
    stem = local_name.split(".", 1)[0].strip().lower()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise InvalidLinkName(
            f"name {local_name!r} is a reserved device name on Windows"
        )

    candidate = _skill_dir(root, local_name) if category == catalog.CATEGORY_SKILL \
        else _rule_md_path(root, local_name)
    try:
        contained = candidate.resolve().is_relative_to(Path(root).resolve())
    except (OSError, ValueError) as exc:
        raise InvalidLinkName(f"name {local_name!r} is not a usable path: {exc}") from exc
    if not contained:
        raise InvalidLinkName(
            f"name {local_name!r} would resolve outside the agent's own folder"
        )


def _skill_dir(root: Path, local_name: str) -> Path:
    return Path(root) / ".claude" / "skills" / local_name


def _skill_md_path(root: Path, local_name: str) -> Path:
    return _skill_dir(root, local_name) / "SKILL.md"


def _rule_md_path(root: Path, local_name: str) -> Path:
    # `local_name` is the mockup-style name and already carries its own
    # `.md` — this is the filename verbatim, no suffix is appended here.
    return Path(root) / ".claude" / "rules" / local_name


def _mcp_json_path(root: Path) -> Path:
    return Path(root) / ".mcp.json"


def _read_mcp_servers(root: Path) -> tuple[dict, dict] | None:
    """``(doc, mcpServers)`` for this agent's ``.mcp.json``, or ``None`` if
    the file is missing/unreadable/malformed. Read-only helper — never
    called from a write path without also handling the "file does not
    parse" case explicitly (see `_merge_mcp_server`).

    A malformed ``.mcp.json`` (hand-corrupted mid-edit, say) falls through
    to ``None`` rather than raising: a file we cannot parse is a file whose
    other content we cannot preserve either way, so `_merge_mcp_server`
    rebuilds it with just this one key instead of refusing outright and
    leaving the agent stuck until a human fixes the JSON by hand. This is
    the one case the `path_conflict` "never overwrite a manual edit" guard
    cannot catch, because it cannot see what is there to conflict with.
    """
    path = _mcp_json_path(root)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    servers = doc.get("mcpServers")
    return doc, (servers if isinstance(servers, dict) else {})


def _check_path_conflict(
    root: Path, category: str, local_name: str, bucket: list[dict[str, Any]]
) -> None:
    """Refuse to materialize at `local_name` when the on-disk target already
    exists and no entry already in `bucket` (this category's own list,
    *before* the current attach/update is applied to it) claims that name.

    Deliberately checked against `bucket` rather than "is this the entry
    being updated": on a rename `bucket` still holds the entry under its OLD
    name, so a new name never matches anything in it and the check runs
    cleanly against the new target; on a same-name update `bucket` already
    holds this exact name, so the check is skipped — the file on disk is
    ours, not evidence of somebody else's manual edit.
    """
    owned = any(link["name"] == local_name for link in bucket)
    if owned:
        return

    if category == catalog.CATEGORY_MCP:
        found = _read_mcp_servers(root)
        if found is not None:
            _, servers = found
            if local_name in servers:
                raise LinkPathConflict(
                    f".mcp.json already has an mcpServers entry named {local_name!r}"
                )
    elif category == catalog.CATEGORY_SKILL:
        if _skill_dir(root, local_name).exists():
            raise LinkPathConflict(
                f".claude/skills/{local_name} already exists"
            )
    elif category == catalog.CATEGORY_RULE:
        if _rule_md_path(root, local_name).exists():
            raise LinkPathConflict(
                f".claude/rules/{local_name} already exists"
            )


def _merge_mcp_server(root: Path, local_name: str, server_config: Any) -> None:
    """Merge ``mcpServers[local_name] = server_config`` into ``.mcp.json``,
    touching only that one key — json.load -> mutate one key -> json.dump,
    never a text patch, so every other server a human already wired by hand
    is left byte-for-byte alone."""
    path = _mcp_json_path(root)
    found = _read_mcp_servers(root)
    doc, servers = found if found is not None else ({}, {})
    servers = dict(servers)
    servers[local_name] = server_config
    doc = dict(doc)
    doc["mcpServers"] = servers
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _unmerge_mcp_server(root: Path, local_name: str) -> None:
    found = _read_mcp_servers(root)
    if found is None:
        return
    doc, servers = found
    if local_name not in servers:
        return
    servers = dict(servers)
    del servers[local_name]
    doc = dict(doc)
    doc["mcpServers"] = servers
    path = _mcp_json_path(root)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _validate_and_materialize(
    root: Path,
    category: str,
    local_name: str,
    entry: catalog.CatalogEntry,
    link_vars: dict[str, str],
    bucket: list[dict[str, Any]],
) -> None:
    """Validate everything about this write, then perform it. Raises
    (`InvalidLinkName`, `LinkPathConflict`, `InvalidSubstitutedConfig`)
    before anything is written — never partway through."""
    if category in (catalog.CATEGORY_SKILL, catalog.CATEGORY_RULE):
        _validate_link_name_path_safe(root, category, local_name)
    _check_path_conflict(root, category, local_name, bucket)

    if category == catalog.CATEGORY_MCP:
        substituted = _substitute_vars(entry.content, link_vars)
        parsed = _parse_substituted_mcp_config(substituted)
        _merge_mcp_server(root, local_name, parsed)
    elif category == catalog.CATEGORY_SKILL:
        path = _skill_md_path(root, local_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entry.content, encoding="utf-8")
        except OSError as exc:
            # A safety net behind `_validate_link_name_path_safe`, not the
            # primary defense — anything that reaches here slipped past the
            # denylist (an OS/filesystem quirk that check does not know
            # about) and must still come back as a clean 400, not a raw 500.
            raise InvalidLinkName(
                f"name {local_name!r} could not be used as a path: {exc}"
            ) from exc
    else:  # catalog.CATEGORY_RULE
        path = _rule_md_path(root, local_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entry.content, encoding="utf-8")
        except OSError as exc:
            raise InvalidLinkName(
                f"name {local_name!r} could not be used as a path: {exc}"
            ) from exc


def _remove_materialization(root: Path, category: str, local_name: str) -> None:
    if category == catalog.CATEGORY_MCP:
        _unmerge_mcp_server(root, local_name)
    elif category == catalog.CATEGORY_SKILL:
        d = _skill_dir(root, local_name)
        if d.exists():
            shutil.rmtree(d)
    elif category == catalog.CATEGORY_RULE:
        with contextlib.suppress(FileNotFoundError):
            _rule_md_path(root, local_name).unlink()


# --------------------------------------------------- links.json: attach/detach


def _norm_link_name(name: str) -> str:
    return name.strip().casefold()


def _link_info(category: str, link: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": link["entry_id"],
        "category": category,
        "name": link["name"],
        "vars": dict(link.get("vars") or {}),
    }


def list_links(slug: str) -> dict[str, list[dict[str, Any]]]:
    """Every link this agent carries, grouped by category."""
    agent = get(slug)
    doc = read_links_config(agent.root)
    return {
        category: [_link_info(category, link) for link in doc.get(category, [])]
        for category in catalog.CATEGORIES
    }


def attach_link(
    slug: str, category: str, entry_id: str, name: str,
    vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Attach a catalog entry to an agent and materialize it write-through.

    Raises `AgentNotFound`, `catalog.EntryNotFound`, `CategoryMismatch`,
    `LinkExists` (duplicate entry_id in this category), `InvalidLinkName`
    (empty name), `LinkNameExists`, `UnknownLinkVar`, `LinkPathConflict`, or
    `InvalidSubstitutedConfig` — nothing is written on any of them.
    """
    if category not in catalog.CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    link_vars = dict(vars or {})

    with _lock:
        agent = get(slug)
        entry = catalog.get(entry_id)
        if entry.category != category:
            raise CategoryMismatch(
                f"catalog entry {entry_id!r} is category {entry.category!r}, "
                f"not {category!r}"
            )

        doc = read_links_config(agent.root)
        bucket = doc[category]

        if any(link["entry_id"] == entry_id for link in bucket):
            raise LinkExists(
                f"{entry_id!r} is already attached to agent {slug!r} in "
                f"category {category!r}"
            )

        clean_name = (name or "").strip()
        if not clean_name:
            raise InvalidLinkName("name must not be empty")
        clash = next(
            (link for link in bucket if _norm_link_name(link["name"]) == _norm_link_name(clean_name)),
            None,
        )
        if clash is not None:
            raise LinkNameExists(
                f"another {category} link is already named {clean_name!r}",
                existing_entry_id=clash["entry_id"],
            )

        unknown = set(link_vars) - set(entry.vars)
        if unknown:
            raise UnknownLinkVar(
                f"unknown var(s) {sorted(unknown)} for catalog entry {entry_id!r} "
                f"(it declares {sorted(entry.vars)})"
            )

        _validate_and_materialize(agent.root, category, clean_name, entry, link_vars, bucket)

        bucket.append({"entry_id": entry_id, "name": clean_name, "vars": link_vars})
        write_links_config(agent.root, doc)
        return _link_info(category, {"entry_id": entry_id, "name": clean_name, "vars": link_vars})


def update_link(
    slug: str, category: str, entry_id: str, *,
    name: str | None = None, vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Edit an existing link in place — full-replace ``vars`` (never a
    merge — the catalog entry's own var set may have changed since attach,
    so a stale caller-side value must not linger). Re-runs the same
    validation and materialization as `attach_link`, against the *current*
    state of the catalog entry.
    """
    if category not in catalog.CATEGORIES:
        raise ValueError(f"unknown category {category!r}")

    with _lock:
        agent = get(slug)
        entry = catalog.get(entry_id)
        if entry.category != category:
            raise CategoryMismatch(
                f"catalog entry {entry_id!r} is category {entry.category!r}, "
                f"not {category!r}"
            )

        doc = read_links_config(agent.root)
        bucket = doc[category]
        idx = next((i for i, link in enumerate(bucket) if link["entry_id"] == entry_id), None)
        if idx is None:
            raise LinkNotFound(
                f"{entry_id!r} is not attached to agent {slug!r} in category {category!r}"
            )
        current = bucket[idx]

        new_name = current["name"] if name is None else name.strip()
        if not new_name:
            raise InvalidLinkName("name must not be empty")
        if name is not None:
            clash = next(
                (
                    link for j, link in enumerate(bucket)
                    if j != idx and _norm_link_name(link["name"]) == _norm_link_name(new_name)
                ),
                None,
            )
            if clash is not None:
                raise LinkNameExists(
                    f"another {category} link is already named {new_name!r}",
                    existing_entry_id=clash["entry_id"],
                )

        new_vars = dict(current["vars"]) if vars is None else dict(vars)
        unknown = set(new_vars) - set(entry.vars)
        if unknown:
            raise UnknownLinkVar(
                f"unknown var(s) {sorted(unknown)} for catalog entry {entry_id!r} "
                f"(it declares {sorted(entry.vars)})"
            )

        renaming = new_name != current["name"]
        _validate_and_materialize(agent.root, category, new_name, entry, new_vars, bucket)
        if renaming:
            _remove_materialization(agent.root, category, current["name"])

        bucket[idx] = {"entry_id": entry_id, "name": new_name, "vars": new_vars}
        write_links_config(agent.root, doc)
        return _link_info(category, bucket[idx])


def detach_link(slug: str, category: str, entry_id: str) -> None:
    """Remove a link and its on-disk materialization."""
    if category not in catalog.CATEGORIES:
        raise ValueError(f"unknown category {category!r}")

    with _lock:
        agent = get(slug)
        doc = read_links_config(agent.root)
        bucket = doc[category]
        idx = next((i for i, link in enumerate(bucket) if link["entry_id"] == entry_id), None)
        if idx is None:
            raise LinkNotFound(
                f"{entry_id!r} is not attached to agent {slug!r} in category {category!r}"
            )
        removed = bucket.pop(idx)
        _remove_materialization(agent.root, category, removed["name"])
        write_links_config(agent.root, doc)


def catalog_entry_used_by(entry_id: str) -> list[str]:
    """Slugs of every agent whose ``links.json`` references ``entry_id`` in
    any category. This is the finder `api.py`'s ``_catalog_used_by`` already
    looks for via ``getattr`` (MN-41) — its presence alone is what turns
    ``DELETE /api/catalog/{id}`` from an always-``[]`` check into a real one.
    """
    slugs: list[str] = []
    for agent in list_agents():
        doc = read_links_config(agent.root)
        for category in catalog.CATEGORIES:
            if any(link["entry_id"] == entry_id for link in doc.get(category, [])):
                slugs.append(agent.slug)
                break
    return slugs


# --------------------------------------------------------------- chats.json
#
# The live-chat metadata index (MN-43) — one JSON file per agent, mirroring
# ``links.json``'s "light" pattern: no cross-process ``_file_lock()`` (this
# is a single agent's own file, not a registry several processes race to
# edit), but every mutation still goes through the module's in-process
# ``_lock`` the way ``attach_link``/``detach_link`` do, so two threads in
# this same backend process cannot tear a read-modify-write of the same
# agent's chat list.
#
# This module owns storage only — chat_id allocation, the JSON index, and
# the on-disk folder shape (``chats/<chat_id>/history.log``). It knows
# nothing about a live PTY process; that is `agent_runtime.py`'s job
# entirely, kept a separate module on purpose (resident process management
# is not a filesystem concern). `delete_chat` in particular does not check
# whether a session for that chat_id is currently live — the caller
# (`api.py`'s `DELETE /api/agents/{slug}/chats/{chat_id}`) is responsible
# for asking `agent_runtime.stop_session` first.


CHATS_VERSION = 1


class ChatNotFound(LookupError):
    """No chat matches the given (agent, chat_id)."""


def _chats_dir(root: Path) -> Path:
    return Path(root) / "chats"


def _chats_index_path(root: Path) -> Path:
    return _chats_dir(root) / "chats.json"


def chat_dir(root: Path, chat_id: str) -> Path:
    """Where one chat's own files live (currently just ``history.log``, but
    named for the folder rather than the one file so a later addition — a
    transcript sidecar, say — has somewhere to go without a new convention).
    """
    return _chats_dir(root) / chat_id


def chat_history_path(root: Path, chat_id: str) -> Path:
    """Append-only raw output log for one chat. Owned here (the storage
    layer decides the path); written to by `agent_runtime.py` (the runtime
    layer decides when)."""
    return chat_dir(root, chat_id) / "history.log"


def subagents_sidecar_path(root: Path, chat_id: str) -> Path:
    """Append-only JSONL log of `SubagentStart`/`SubagentStop` hook events
    for one chat (MN-45b) — one JSON object per line, one line per hook
    event `agent_runtime.record_subagent_event` receives. The Start and the
    later Stop each get their own line, matching `history.log`'s
    append-only shape rather than merging the pair into one record. Owned
    here for the same reason `chat_history_path` is (the storage layer
    decides the path); written to by `agent_runtime.py`, read back by
    `read_subagent_events` below and by `api.py`'s subagent-events GET
    route."""
    return chat_dir(root, chat_id) / "subagents.jsonl"


def read_subagent_events(root: Path, chat_id: str) -> list[dict[str, Any]]:
    """Parse this chat's `subagents.jsonl` sidecar into a list of event
    dicts, in file order. No sidecar yet (no subagent has run in this chat)
    is an empty list, not an error — the same "absent means nothing yet"
    convention `api.py`'s `_read_agent_subagents` uses for a missing
    `.claude/agents/` folder. A line that fails to parse is skipped rather
    than raised — one bad line must not fail the whole listing."""
    path = subagents_sidecar_path(root, chat_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _default_chats_doc() -> dict:
    return {"version": CHATS_VERSION, "chats": []}


def _read_chats_doc(root: Path) -> dict:
    path = _chats_index_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default_chats_doc()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("chats"), list):
        raise ValueError(f"{path}: expected an object with a 'chats' array")
    return {"version": CHATS_VERSION, "chats": list(data["chats"])}


def _write_chats_doc(root: Path, doc: dict) -> None:
    path = _chats_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(tmp, path)


def _chat_info(chat: dict) -> dict[str, Any]:
    return {
        "chat_id": chat["chat_id"],
        "title": chat.get("title"),
        "created_at": chat.get("created_at", ""),
        "last_active_at": chat.get("last_active_at", ""),
    }


def list_chats(slug: str) -> list[dict[str, Any]]:
    """Every chat this agent has, most-recently-active first. Raises
    `AgentNotFound`.

    Sorted over the REVERSED append order, not the append order itself:
    ``last_active_at`` is second-precision (``_now_iso``'s
    ``timespec="seconds"``, the convention this whole module uses), so two
    chats touched in the same second tie on the sort key. A stable sort over
    the reversed list keeps the most-recently-appended one first among ties
    — the only tiebreak that reads as "most recent" to a human, instead of
    silently depending on `chats.json`'s on-disk order.
    """
    agent = get(slug)
    doc = _read_chats_doc(agent.root)
    chats = sorted(
        reversed(doc["chats"]), key=lambda c: c.get("last_active_at") or "", reverse=True
    )
    return [_chat_info(c) for c in chats]


def get_chat(slug: str, chat_id: str) -> dict[str, Any]:
    """Raises `AgentNotFound` or `ChatNotFound`."""
    agent = get(slug)
    doc = _read_chats_doc(agent.root)
    for chat in doc["chats"]:
        if chat.get("chat_id") == chat_id:
            return _chat_info(chat)
    raise ChatNotFound(f"no chat {chat_id!r} for agent {slug!r}")


def create_chat(slug: str, title: str | None = None) -> dict[str, Any]:
    """Create a new chat record and its empty folder. Cheap and side-effect
    free beyond that — no ``claude`` process is spawned here. A real PTY only
    starts on the first WebSocket subscriber
    (`agent_runtime.ensure_and_subscribe`), because chat-record creation is
    free and a real spawn costs a paid API call.

    Raises `AgentNotFound`.
    """
    with _lock:
        agent = get(slug)
        chat_id = uuid.uuid4().hex
        now = _now_iso()
        chat = {
            "chat_id": chat_id,
            "title": (title or "").strip() or None,
            "created_at": now,
            "last_active_at": now,
        }
        doc = _read_chats_doc(agent.root)
        doc["chats"].append(chat)
        chat_dir(agent.root, chat_id).mkdir(parents=True, exist_ok=True)
        _write_chats_doc(agent.root, doc)
        return _chat_info(chat)


def touch_chat(slug: str, chat_id: str) -> dict[str, Any]:
    """Bump ``last_active_at`` to now. Called by `agent_runtime` around a
    session's spawn and teardown — deliberately not on every PTY output
    chunk, which would turn a JSON read-modify-write into the hot path of
    every keystroke a live session produces.

    Raises `AgentNotFound` or `ChatNotFound`.
    """
    with _lock:
        agent = get(slug)
        doc = _read_chats_doc(agent.root)
        for chat in doc["chats"]:
            if chat.get("chat_id") == chat_id:
                chat["last_active_at"] = _now_iso()
                _write_chats_doc(agent.root, doc)
                return _chat_info(chat)
        raise ChatNotFound(f"no chat {chat_id!r} for agent {slug!r}")


def delete_chat(slug: str, chat_id: str) -> None:
    """Remove a chat's record and its on-disk folder (``history.log`` and
    everything else under it). Purely storage — does not know whether a live
    PTY session for this ``chat_id`` exists; see the module note above.

    Raises `AgentNotFound` or `ChatNotFound`.
    """
    with _lock:
        agent = get(slug)
        doc = _read_chats_doc(agent.root)
        remaining = [c for c in doc["chats"] if c.get("chat_id") != chat_id]
        if len(remaining) == len(doc["chats"]):
            raise ChatNotFound(f"no chat {chat_id!r} for agent {slug!r}")
        doc["chats"] = remaining
        _write_chats_doc(agent.root, doc)
        with contextlib.suppress(FileNotFoundError):
            shutil.rmtree(chat_dir(agent.root, chat_id))


# ------------------------------------------------------------ folder shape


def _materialize_agent_tree(root: Path, claude_md: str | None, *, adopt: bool) -> list[str]:
    """Build the agent tree, creating only what is absent.

    Mirrors `scaffold._seed_tree`'s "create only if absent" shape: nothing
    here ever overwrites a file that is already on disk. ``adopt`` changes no
    write here — a fresh `create()` and an `adopt()` of an empty folder do the
    same work — it exists so the log reads honestly for whichever call this
    is.
    """
    log_lines: list[str] = []
    root.mkdir(parents=True, exist_ok=True)

    claude_dir = root / ".claude"
    for d in (claude_dir / "rules", claude_dir / "skills"):
        if not d.exists():
            d.mkdir(parents=True)
            log_lines.append(f"  created              {d}")

    memory_dir = root / "memory"
    if not memory_dir.exists():
        memory_dir.mkdir(parents=True)
        log_lines.append(f"  created              {memory_dir}")

    claude_md_path = root / "CLAUDE.md"
    if not claude_md_path.exists():
        text = claude_md if claude_md else f"# {root.name}\n"
        claude_md_path.write_text(text, encoding="utf-8")
        log_lines.append(f"  created              {claude_md_path}")
    elif claude_md is not None:
        # Adoption never overwrites: a CLAUDE.md the project already had wins
        # over whatever the caller supplied.
        log_lines.append(
            f"  kept                 {claude_md_path} (already present — "
            f"supplied content was not written)"
        )

    launch_path = root / "launch.json"
    if not launch_path.exists():
        write_launch_config(root, {"mode": "standard"})
        log_lines.append(f"  created              {launch_path}")

    mcp_path = root / ".mcp.json"
    if not mcp_path.exists():
        mcp_path.write_text(
            json.dumps({"mcpServers": {}}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        log_lines.append(f"  created              {mcp_path}")

    _ = adopt  # log-only distinction; the writes above are identical either way
    return log_lines


def preview_adopt(root: Path | str) -> dict:
    """Read-only inspection of a candidate folder — zero side effects, safe
    to call repeatedly. What `create()`/`adopt()` would find, before either
    writes anything.
    """
    root = Path(root).expanduser()
    resolved = root.resolve() if root.exists() else root

    root_exists = resolved.is_dir()
    entries = list(resolved.iterdir()) if root_exists else []
    empty = root_exists and not entries

    already_registered_agent = None
    if root_exists:
        for agent in list_agents():
            with contextlib.suppress(OSError):
                if agent.root.resolve() == resolved:
                    already_registered_agent = agent.slug
                    break

    claude_md_path = resolved / "CLAUDE.md"
    has_claude_md = claude_md_path.is_file()
    claude_md_excerpt = None
    if has_claude_md:
        with contextlib.suppress(OSError):
            claude_md_excerpt = claude_md_path.read_text(
                encoding="utf-8", errors="replace"
            )[:500]

    mcp_path = resolved / ".mcp.json"
    has_mcp_json = mcp_path.is_file()
    mcp_server_names: list[str] = []
    if has_mcp_json:
        try:
            doc = json.loads(mcp_path.read_text(encoding="utf-8"))
            servers = doc.get("mcpServers")
            if isinstance(servers, dict):
                mcp_server_names = sorted(servers)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    claude_dir = resolved / ".claude"
    has_claude_dir = claude_dir.is_dir()
    rule_files: list[str] = []
    rules_dir = claude_dir / "rules"
    if rules_dir.is_dir():
        rule_files = sorted(p.name for p in rules_dir.iterdir() if p.is_file())
    skill_dirs: list[str] = []
    skills_dir = claude_dir / "skills"
    if skills_dir.is_dir():
        skill_dirs = sorted(p.name for p in skills_dir.iterdir() if p.is_dir())

    memory_dir = resolved / "memory"
    has_memory = memory_dir.is_dir()
    memory_already_bank = False
    if has_memory:
        try:
            registry.get(config.bank_id(memory_dir))
            memory_already_bank = True
        except registry.BankNotFound:
            memory_already_bank = False

    existing_slugs = [a.slug for a in list_agents()]
    suggested_name = resolved.name or str(resolved)
    suggested_slug = unique_slug(slugify(suggested_name), existing_slugs)

    return {
        "root_exists": root_exists,
        "empty": empty,
        "already_registered_agent": already_registered_agent,
        "has_claude_md": has_claude_md,
        "claude_md_excerpt": claude_md_excerpt,
        "has_mcp_json": has_mcp_json,
        "mcp_server_names": mcp_server_names,
        "has_claude_dir": has_claude_dir,
        "rule_files": rule_files,
        "skill_dirs": skill_dirs,
        "has_memory": has_memory,
        "memory_already_bank": memory_already_bank,
        "suggested_slug": suggested_slug,
        "suggested_name": suggested_name,
    }


# ------------------------------------------------------------------ mutate


def create(name: str, root: Path | str | None = None,
           claude_md: str | None = None) -> Agent:
    """Create a brand-new agent.

    ``owns_root=True`` — the one thing that gives `delete()` permission to
    ``rmtree`` — is granted **only** for the default location
    (``root`` omitted, so the folder is ``config.AGENTS_DIR / slug`` and
    nothing else could have put anything there first). An explicit ``root``,
    even an empty or freshly-created one, always gets ``owns_root=False``:
    the caller chose that path, so it is the caller's, and `delete()` will
    never remove it — only `adopt()`'s and this function's *default-location*
    branch ever hand out destructive-cleanup rights. (Narrowed 2026-08-29
    after an independent review reproduced a live data-loss bug in the wider
    rule — see MN-40's review comment.)

    Refuses when the target already exists and is not empty — that shape
    belongs to `adopt()`. Checked here again regardless of what the caller
    (the API layer's ``confirm_adopt``) believes, because this is the layer
    that actually writes.
    """
    with _lock, _file_lock():
        agents = load(force=True)
        slug = unique_slug(slugify(name), (a.slug for a in agents))

        owns_root = root is None
        resolved = (
            (config.AGENTS_DIR / slug) if root is None
            else Path(root).expanduser().resolve()
        )
        if resolved.exists() and any(resolved.iterdir()):
            raise AgentExists(
                f"{resolved} already exists and is not empty — use adopt() instead"
            )

        _materialize_agent_tree(resolved, claude_md, adopt=False)
        registry.add(resolved / "memory")

        agent = Agent(
            slug=slug,
            name=name.strip() or slug,
            root=resolved,
            owns_root=owns_root,
            created_at=_now_iso(),
        )
        agents.append(agent)
        save(agents)
        return agent


def adopt(root: Path | str, name: str, claude_md: str | None = None) -> Agent:
    """Adopt an existing folder as an agent. ``owns_root=False``: `delete()`
    will never touch the folder or anything already in it, only this
    module's own records.

    If the folder's ``memory/`` is already a registered bank under a
    different name, `registry.add`'s ``BankExists`` is let through as-is —
    this layer does not decide what to do about a pre-existing bank, the
    caller does.
    """
    with _lock, _file_lock():
        agents = load(force=True)
        slug = unique_slug(slugify(name), (a.slug for a in agents))
        resolved = Path(root).expanduser().resolve()

        _materialize_agent_tree(resolved, claude_md, adopt=True)
        registry.add(resolved / "memory")

        agent = Agent(
            slug=slug,
            name=name.strip() or slug,
            root=resolved,
            owns_root=False,
            created_at=_now_iso(),
        )
        agents.append(agent)
        save(agents)
        return agent


def delete(slug: str) -> None:
    """Unregister an agent, and — only when it owns its folder — delete the
    folder too.

    This is the one place a wrong answer costs someone a project. Two things
    make the ``rmtree`` branch safe against a hand-edited (or corrupted)
    ``agents.json`` — ``agents.json`` being editable by hand is a documented
    property of this store, same as ``banks.json``, so this is not a
    contrived threat model:

    * ``owns_root`` alone is not trusted — `create()` only ever grants it for
      the default location, so the guard below re-derives the ONE path that
      location can be and checks **exact equality**, not merely "somewhere
      under AGENTS_DIR". A tampered entry claiming some *other* agent's
      folder (``root`` = a legitimate sibling's path, ``owns_root: true``)
      fails this check — that sibling's folder was never
      ``AGENTS_DIR / <this slug>`` — and the delete is refused rather than
      silently destroying someone else's folder. (This exact attack was
      reproduced live during MN-40's review before this guard existed.)
    * The guard raises ``RuntimeError``, never ``assert`` — an ``assert`` in
      front of ``shutil.rmtree`` is stripped entirely under
      ``-O``/``PYTHONOPTIMIZE=1`` (CWE-617), which is the wrong failure mode
      for a check standing in front of a destructive filesystem call.

    An adopted agent's folder (``owns_root=False`` — every agent created
    with an explicit ``root`` gets this, not only `adopt()`'s) is NEVER
    removed, only the registry entries naming it.

    Order matters: the filesystem step (when it happens) runs BEFORE any
    registry entry is touched, and nothing below is wrapped to swallow its
    exception. A failed ``rmtree`` therefore leaves the agent still listed,
    the bank still registered and the error surfaced — a visibly incomplete
    delete the caller can retry — rather than a registry that says the agent
    is gone while its folder (or part of it) is still on disk.
    """
    with _lock, _file_lock():
        agents = load(force=True)
        idx = next(
            (i for i, a in enumerate(agents) if _norm_slug(a.slug) == _norm_slug(slug)),
            None,
        )
        if idx is None:
            raise AgentNotFound(f"no agent with slug {slug!r}")
        agent = agents[idx]

        if agent.owns_root:
            expected = config.AGENTS_DIR / agent.slug
            if agent.root != expected:
                raise RuntimeError(
                    f"refusing to delete {agent.root}: owns_root=True but "
                    f"its root is not {expected} (the only location "
                    f"`create()` ever grants ownership of) — the agents.json "
                    f"entry for {agent.slug!r} looks corrupted or tampered "
                    f"with; nothing was deleted"
                )
            shutil.rmtree(agent.root)

        # Reached only once the filesystem step above either succeeded or
        # was not needed — see the ordering note in the docstring.
        try:
            registry.remove(agent.bank_id, drop_index=True)
        except registry.BankNotFound:
            log.warning(
                "agent %s: no bank %s to remove (already gone?)",
                agent.slug, agent.bank_id,
            )

        del agents[idx]
        save(agents)
