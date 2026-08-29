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
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config, registry

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
