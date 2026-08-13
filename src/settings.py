"""Machine settings — ``STATE_DIR/settings.json`` (Memory-contracts-v3 §6.5).

The same shape as the banks registry, one level up: ``banks.json`` configures
a *bank*, this configures the *machine*. Both are hand-editable JSON the
service re-reads when the file changes, and both preserve keys they do not
own.

**Precedence: environment > file > code default.** A variable exported for one
run must still beat a stored value, or scripts and CI stop being predictable.
The cost is that a stored value can be silently inert, so `effective()`
reports *where* each value came from and the cabinet is expected to say so —
otherwise a click appears to do nothing and the page is lying.

**What belongs here is only what a person genuinely configures**: which
provider, and the endpoint that provider needs. The other ~30 knobs stay
environment-only debug levers. Two were considered and deliberately left out:

* the **API port** — the cabinet reaches the service *through* it, and every
  project's ``.mcp.json`` holds it, so changing it from a form would cut the
  page off from its own backend and break wiring the form cannot see. It is
  an installer-level decision, shown here but not editable.
* **``pad_budget``** — a measured property of a backend, not a preference, and
  a wrong value costs 2x silently (`providers.base.pad_budget`). It arrives
  here when the calibration button can measure and write it.

**Everything is a function, never an import-time constant.** ``config.py``
evaluates its knobs once at import; a value the cabinet can edit cannot be one
of those. This is the same scar as ``BANKS_FILE`` — a constant derived from
``STATE_DIR`` froze the path and leaked empty databases into the real state
directory. So: call ``provider()``, never ``from .settings import PROVIDER``.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger("mnemo.settings")

SETTINGS_VERSION = 1

# Fields this module owns. Anything else in the document is carried through a
# rewrite untouched — a note somebody added by hand is not ours to drop.
_KNOWN_KEYS = frozenset({"version", "provider", "api"})
_KNOWN_API_KEYS = frozenset({"url", "model", "dim", "key", "timeout"})

_lock = threading.RLock()
_cache: dict[str, Any] | None = None
_cache_sig: tuple[str, int | None, int | None] | None = None


class SettingsInvalid(ValueError):
    """The document exists but cannot be read as settings.

    Raised rather than falling back to defaults: a typo in this file must not
    silently move the machine back onto the local provider after the user
    pointed it at an endpoint.
    """


# ------------------------------------------------------------------- file


def settings_file() -> Path:
    """Location of the document, resolved per call.

    Per call, not once at import: ``STATE_DIR`` is itself environment-driven
    and a test (or a container) may relocate it after this module is imported.
    """
    override = os.environ.get("MNEMO_SETTINGS_FILE")
    return Path(override) if override else Path(config.STATE_DIR) / "settings.json"


def _signature(path: Path) -> tuple[str, int | None, int | None]:
    try:
        st = path.stat()
    except OSError:
        return (str(path), None, None)
    return (str(path), st.st_mtime_ns, st.st_size)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}          # absence is the normal case, not an error
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SettingsInvalid(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise SettingsInvalid(f"{path}: expected a JSON object")
    api = doc.get("api")
    if api is not None and not isinstance(api, dict):
        raise SettingsInvalid(f"{path}: 'api' must be an object")
    return doc


def load(*, force: bool = False) -> dict[str, Any]:
    """The stored document, re-read whenever the file changed on disk."""
    global _cache, _cache_sig
    with _lock:
        path = settings_file()
        sig = _signature(path)
        if not force and _cache is not None and _cache_sig == sig:
            return dict(_cache)
        doc = _read(path)
        _cache, _cache_sig = doc, sig
        return dict(doc)


def save(doc: dict[str, Any]) -> Path:
    """Write atomically, preserving keys we do not own.

    The file is created only when something deviates from the defaults; an
    absent file is the normal state and `save({})` still writes, because
    "explicitly nothing" is a choice the caller made.
    """
    global _cache, _cache_sig
    with _lock:
        path = settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        merged = dict(_read(path))          # whatever a human put there
        merged.update(doc)
        merged["version"] = SETTINGS_VERSION
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # May carry an API key, so it gets the same treatment as banks.json:
        # narrowed before the rename, never briefly world-readable. POSIX-only
        # in effect; on Windows the user-profile ACL is the real protection.
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        _cache, _cache_sig = merged, _signature(path)
        return path


# ------------------------------------------------------------ resolution


@dataclass(frozen=True)
class Value:
    """One resolved setting, and where it came from.

    ``source`` is why this type exists: with environment > file, a value the
    cabinet stored can be overridden and therefore inert. A form that cannot
    say so shows the user a field that does nothing when they save it.
    """

    value: Any
    source: str          # 'env' | 'file' | 'default'
    env_var: str = ""    # the variable responsible, when source == 'env'

    @property
    def overridden(self) -> bool:
        return self.source == "env"


def _resolve(
    env_var: str, path: tuple[str, ...], default: Any, *, empty_is_a_value: bool = False
) -> Value:
    """environment > file > default, for one key.

    ``empty_is_a_value`` distinguishes "not set" from "deliberately blank".
    Normally an empty string means unset — an exported-but-empty variable is
    almost always an accident, and treating it as a value would blank out a
    URL. Prefixes are the exception: "" is how a person says *this model takes
    no markers*, overriding what the catalogue believes, and that instruction
    has to survive.
    """
    raw = os.environ.get(env_var)
    if raw is not None and (empty_is_a_value or raw != ""):
        return Value(raw, "env", env_var)
    node: Any = load()
    missing = object()
    found: Any = missing
    for part in path:
        if not isinstance(node, dict) or part not in node:
            break
        node = node[part]
    else:
        found = node
    if found is not missing and found is not None:
        if empty_is_a_value or found != "":
            return Value(found, "file")
    return Value(default, "default")


def _as_int(value: Value) -> Value:
    try:
        return Value(int(value.value), value.source, value.env_var)
    except (TypeError, ValueError):
        return Value(0, value.source, value.env_var)


def _as_float(value: Value, fallback: float) -> Value:
    try:
        return Value(float(value.value), value.source, value.env_var)
    except (TypeError, ValueError):
        return Value(fallback, value.source, value.env_var)


def provider() -> str:
    """Which embedding provider the service uses: ``local`` | ``api``.

    A bank may still override this for itself (the registry's ``provider``
    field); this is the machine-wide default.
    """
    return str(_resolve("MNEMO_PROVIDER", ("provider",), "local").value)


def api_url() -> str:
    return str(_resolve("MNEMO_API_EMBED_URL", ("api", "url"), "").value)


def api_model() -> str:
    return str(_resolve("MNEMO_API_EMBED_MODEL", ("api", "model"), "").value)


def api_dim() -> int:
    """Vector width of the configured endpoint. 0 means "not configured".

    Never guessed: the sqlite-vec column is declared at a fixed width, so a
    wrong value is not slow degradation but a corrupt index.
    """
    return int(_as_int(_resolve("MNEMO_API_EMBED_DIM", ("api", "dim"), 0)).value)


def api_key() -> str:
    return str(_resolve("MNEMO_API_EMBED_KEY", ("api", "key"), "").value)


def api_passage_prefix(default: str = "") -> str:
    """Marker prepended to documents, when the model was trained with one.

    ``default`` comes from the model catalogue, so the usual path needs no
    setting at all. An explicit value wins in both directions — it can supply
    markers for a model we have not catalogued, or clear them for one we
    catalogued wrongly.
    """
    return str(
        _resolve("MNEMO_API_PASSAGE_PREFIX", ("api", "passage_prefix"), default,
                 empty_is_a_value=True).value
    )


def api_query_prefix(default: str = "") -> str:
    """Marker prepended to queries. See ``api_passage_prefix``."""
    return str(
        _resolve("MNEMO_API_QUERY_PREFIX", ("api", "query_prefix"), default,
                 empty_is_a_value=True).value
    )


def api_timeout() -> float:
    return float(
        _as_float(
            _resolve("MNEMO_API_EMBED_TIMEOUT", ("api", "timeout"), 60.0), 60.0
        ).value
    )


def effective() -> dict[str, Value]:
    """Every setting with its resolved value AND its origin.

    What the cabinet's settings page renders: the value to show, plus whether
    an environment variable is overriding what the file says, so a stored
    value that cannot take effect is visible rather than mysterious.
    """
    return {
        "provider": _resolve("MNEMO_PROVIDER", ("provider",), "local"),
        "api.url": _resolve("MNEMO_API_EMBED_URL", ("api", "url"), ""),
        "api.model": _resolve("MNEMO_API_EMBED_MODEL", ("api", "model"), ""),
        "api.dim": _as_int(_resolve("MNEMO_API_EMBED_DIM", ("api", "dim"), 0)),
        "api.timeout": _as_float(
            _resolve("MNEMO_API_EMBED_TIMEOUT", ("api", "timeout"), 60.0), 60.0
        ),
        # The key's presence is reportable; its value is not. A settings page
        # that echoes a secret back puts it in a screenshot.
        "api.key_set": Value(
            bool(api_key()),
            _resolve("MNEMO_API_EMBED_KEY", ("api", "key"), "").source,
            "MNEMO_API_EMBED_KEY",
        ),
    }
