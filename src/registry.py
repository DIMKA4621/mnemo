"""Bank registry — the list of memory roots this machine's service serves.

One JSON file, ``STATE_DIR/banks.json`` (Memory-contracts-v3 §6). It is a
human-readable, hand-editable document on purpose: a user may open it, add a
root, fix a name — and the running service picks the change up, because every
read re-checks the file's mtime instead of trusting a process-lifetime cache.

Two properties this module enforces rather than assumes:

* **``id`` is derived, never chosen** — ``sha1(canonical root)[:16]`` via
  ``config.bank_id``, so one folder can never end up with two databases.
* **``name`` is unique** — the name, not the id, is the public address of a
  bank (it is what goes into a git-tracked ``.mcp.json``; the id is a hash of
  a path on one machine and means nothing after a clone). ``add`` suffixes a
  collision (``notes`` -> ``notes-2``); ``update`` refuses one, because a
  silent rename would break the wiring that points at the old name.

* **each bank carries its own token** — minted when the bank is first
  registered and stored next to it. It opens that bank's MCP face and nothing
  else, so a project's git-ignored wiring never has to hold the service-wide
  credential. This is **least privilege, not a wall**: the service token still
  opens every bank, and anyone who can read `banks.json` can read every token
  in it. What it buys is that handing a colleague one project's `.mcp.env` does
  not hand them every other bank on the machine.

Unknown fields survive a rewrite: a note somebody added by hand is not data
we own, so we carry it through instead of dropping it.
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
from .config import bank_id as derive_bank_id

log = logging.getLogger("mnemo.registry")

# Registry file format. Bumped only if the document shape changes; the bank
# object itself grows by adding fields, which old readers preserve anyway.
REGISTRY_VERSION = 1

# Directories that carry no identity of their own: a bank rooted at
# ``<proj>/.claude/memory`` is "the <proj> bank", not "the memory bank".
_WIRING_DIRS = frozenset({".claude", "memory", "agent-memory"})

_HEX16 = re.compile(r"^[0-9a-f]{16}$")

# Fields this module owns. Anything else found in a bank object is preserved
# verbatim under ``Bank.extra`` and written back on save.
#
# ``enabled`` is listed although nothing writes it any more: it is the field
# ``state`` replaced, and a registry written before that change still carries
# it. Leaving it out would send it to ``extra``, which round-trips — so the
# file would keep a stale boolean next to the state it was converted into,
# and a hand-edit to one of them would silently disagree with the other.
# Listed here, it is read once and then gone from the document.
_KNOWN_FIELDS = frozenset(
    {"id", "name", "root", "provider", "state", "enabled",
     "exclude", "added_at", "token"}
)

# The three states a bank can be in (§6.2). One field, because two booleans
# would be two things saying the same thing and free to disagree.
#
#   enabled   watched, indexed, searchable — the normal state
#   frozen    NOT watched, still searchable: the index is deliberately held
#             still. What it buys is experiments with the machine's embedding
#             backend, which otherwise rebuild every bank on the machine.
#   disabled  neither watched nor searchable — off, but still registered.
STATE_ENABLED = "enabled"
STATE_FROZEN = "frozen"
STATE_DISABLED = "disabled"
STATES = (STATE_ENABLED, STATE_FROZEN, STATE_DISABLED)

# Same generator and same shape as the service token (`api.api_token`) and as
# `embed.token`: 48 hex characters. One shape for every credential mnemo mints
# means one thing to recognise in a file and one thing to redact in a log.
TOKEN_HEX_BYTES = 24


def new_token() -> str:
    return secrets.token_hex(TOKEN_HEX_BYTES)

_lock = threading.RLock()
_cache: list[Bank] | None = None
_cache_sig: tuple[str, int | None, int | None] | None = None
_doc_extra: dict[str, Any] = {}


class BankNotFound(LookupError):
    """No registered bank matches the reference."""


class BankExists(ValueError):
    """A bank with this root — or this name — is already registered."""


class AmbiguousBankRef(LookupError):
    """The reference matches more than one bank.

    Only reachable through a hand-edited file: ``add`` / ``update`` keep names
    unique. We refuse to guess rather than pick one at random.
    """


# --------------------------------------------------------------- settings


def _default_exclude() -> list[str]:
    """Glob patterns skipped when walking a bank root."""
    return list(
        getattr(
            config,
            "DEFAULT_EXCLUDE",
            [".git/**", ".venv/**", "node_modules/**", "__pycache__/**"],
        )
    )


def state_dir() -> Path:
    """The writable state directory, looked up **through the module**.

    ``from .config import STATE_DIR`` would bind the ``Path`` object once at
    import, so a test or a container that later points ``config.STATE_DIR``
    somewhere else would keep writing to the original directory — which is
    how empty databases leaked into the user's real state dir. Every path
    below is derived from this call, never from an import-time binding.
    """
    return Path(config.STATE_DIR)


def banks_file() -> Path:
    """Location of the registry document, resolved per call (see `state_dir`)."""
    override = getattr(config, "BANKS_FILE_OVERRIDE", None) or os.environ.get(
        "MNEMO_BANKS_FILE"
    )
    return Path(override) if override else state_dir() / "banks.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _canonical(path: Path) -> str:
    """Same canonicalisation ``config.bank_id`` hashes — POSIX, and
    case-folded on Windows, so ``E:\\Work`` and ``e:/work`` are one bank."""
    canonical = path.expanduser().resolve().as_posix()
    return canonical.lower() if os.name == "nt" else canonical


def _norm_name(name: str) -> str:
    """Names are addresses: compared stripped and case-insensitively."""
    return name.strip().casefold()


def default_name(root: Path) -> str:
    """Human name for a root: the nearest enclosing folder that identifies it.

    ``.../odin-crm/.claude/memory`` -> ``odin-crm``. Walking past the wiring
    folders matters because otherwise every project on the machine would want
    to be called ``memory`` and the uniqueness suffixes would carry all the
    meaning.

    Used to special-case ``mnemo`` as wiring when it sat directly under
    ``.claude`` (the installed engine's own folder), so a *project* actually
    named ``mnemo`` — this repository — wasn't swallowed and renamed after
    its grandparent. Moot since the engine home moved to ``~/.mnemo``
    (2026-08-21): nothing a bank root walks through can collide with that
    shape any more, so the carve-out is gone rather than left checking for
    a location that no longer exists.
    """
    node = root.expanduser().resolve()
    while node.parent != node and (
        node.name.casefold() in _WIRING_DIRS
        or node.name.startswith(".")
    ):
        node = node.parent
    return node.name or root.expanduser().resolve().as_posix().strip("/:")


# ------------------------------------------------------------------- model


@dataclass(frozen=True)
class Bank:
    """One registered memory root."""

    id: str
    name: str
    root: Path
    provider: str | None = None
    state: str = STATE_ENABLED
    exclude: list[str] = field(default_factory=_default_exclude)
    added_at: str = ""
    # This bank's own MCP credential. Empty only for a bank registered before
    # tokens existed and not yet migrated (`ensure_tokens`) — never empty for
    # a bank this module created.
    token: str = ""
    # Fields a human (or a future version) put in banks.json that we do not
    # understand. Round-tripped untouched.
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    # The three questions the rest of the codebase actually asks. They read as
    # what the caller means, so a site that must change when a state is added
    # is one that genuinely cares — not every `== "enabled"` in the tree.
    #
    # `enabled` is kept because it is what a dozen call sites already ask, and
    # because it is exactly "the fully normal state". It is a property now, so
    # it cannot be assigned: a state is set through `state`, in one place.

    @property
    def enabled(self) -> bool:
        """Fully on — watched, indexed and searchable."""
        return self.state == STATE_ENABLED

    @property
    def watched(self) -> bool:
        """Whether the watcher follows it and background work runs for it.

        False for a frozen bank: holding the index still is the whole point.
        An explicit reindex is a different question — see `api_reindex`.
        """
        return self.state == STATE_ENABLED

    @property
    def searchable(self) -> bool:
        """Whether search and the MCP faces may reach it.

        True for a frozen bank — a held index is still an index — and False
        only for a disabled one.
        """
        return self.state != STATE_DISABLED

    @property
    def db_path(self) -> Path:
        return state_dir() / f"{self.id}.db"

    @property
    def exists(self) -> bool:
        return self.root.is_dir()

    @property
    def is_git(self) -> bool:
        """True when the root sits inside a git work tree (``.git`` may be a
        directory or, for a worktree/submodule, a file)."""
        node = self.root
        while True:
            if (node / ".git").exists():
                return True
            if node.parent == node:
                return False
            node = node.parent

    def to_json(self) -> dict[str, Any]:
        """Serialised form — unknown fields first so ours always read last."""
        data: dict[str, Any] = dict(self.extra)
        data.update(
            {
                "id": self.id,
                "name": self.name,
                "root": self.root.as_posix(),
                "provider": self.provider,
                # `state` only. Writing a derived `enabled` alongside it would
                # put a second source of truth into a file advertised as
                # hand-editable, and the two are free to disagree the moment
                # somebody edits one of them.
                "state": self.state,
                "exclude": list(self.exclude),
                "added_at": self.added_at,
                "token": self.token,
            }
        )
        return data


def _read_state(obj: dict[str, Any], name: Any) -> str:
    """The bank's state, reading both the current and the pre-`state` shape.

    ``state`` wins when present. Otherwise the boolean it replaced is
    translated (``false`` -> disabled), which is exact: before `frozen`
    existed, ``enabled: false`` meant not watched **and** not searchable.

    An unrecognised value falls back to ``enabled`` with a warning rather
    than to ``disabled``. banks.json is advertised as hand-editable, so a
    typo is a realistic input — and the safe direction for a typo is a bank
    that still answers, not one that silently stops.
    """
    raw = obj.get("state")
    if raw is not None:
        value = str(raw).strip().lower()
        if value in STATES:
            return value
        log.warning(
            "bank %r: unknown state %r — treating it as %r (expected one of %s)",
            name, raw, STATE_ENABLED, ", ".join(STATES),
        )
        return STATE_ENABLED
    # Pre-`state` registry.
    return STATE_ENABLED if bool(obj.get("enabled", True)) else STATE_DISABLED


def _from_json(obj: dict[str, Any]) -> Bank:
    root = Path(str(obj["root"])).expanduser()
    # The id is a pure function of the root (§6.2), so the stored value is a
    # cache, not an input — always re-derive it. Trusting the file instead
    # would mean a hand-edited `root` keeps the old id and the bank then
    # serves the NEW tree out of the OLD root's chunks, silently. banks.json
    # is advertised as hand-editable, so this has to be safe by construction.
    # The old <id>.db is simply orphaned; the index is disposable and the new
    # id rebuilds from the .md.
    derived = derive_bank_id(root)
    stored = str(obj.get("id") or derived)
    if stored != derived:
        log.warning(
            "bank %r: id %s does not match root %s — using the derived id %s",
            obj.get("name"), stored, root.as_posix(), derived,
        )
    return Bank(
        id=derived,
        name=str(obj["name"]),
        root=root,
        provider=obj.get("provider"),
        state=_read_state(obj, obj.get("name")),
        exclude=list(obj.get("exclude") or _default_exclude()),
        added_at=str(obj.get("added_at") or ""),
        # Read, never minted here: `load` is on every request path and must
        # not write. Filling a blank is `ensure_tokens` / `token_for`, both of
        # which take the cross-process lock.
        token=str(obj.get("token") or ""),
        extra={k: v for k, v in obj.items() if k not in _KNOWN_FIELDS},
    )


# -------------------------------------------------------------- load / save


# `_lock` only serialises threads inside one process, but the CLI, the service
# and a human with an editor all write this file. `os.replace` guarantees the
# file is never torn — yet two concurrent read-modify-writes still lose one of
# the two registrations, silently, which is worse than a visible error. So
# every mutation takes a cross-process lock file for the whole cycle.
_LOCK_TIMEOUT_S = 10.0
_LOCK_STALE_S = 60.0


@contextlib.contextmanager
def _file_lock():
    """Exclusive lock around a read-modify-write of banks.json."""
    path = banks_file().with_name(banks_file().name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    handle = None
    while True:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            # A crashed holder must not wedge the registry forever.
            try:
                if time.time() - path.stat().st_mtime > _LOCK_STALE_S:
                    log.warning("breaking stale registry lock %s", path)
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"registry is locked by another process ({path})")
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


def _read(path: Path) -> tuple[list[Bank], dict[str, Any]]:
    if not path.exists():
        return [], {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # Refusing loudly beats starting with an empty registry and letting
        # the service quietly forget every bank the user registered.
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("banks"), list):
        raise ValueError(f"{path}: expected an object with a 'banks' array")
    banks = [_from_json(o) for o in doc["banks"] if isinstance(o, dict)]
    extra = {k: v for k, v in doc.items() if k not in ("version", "banks")}
    return banks, extra


def load(*, force: bool = False) -> list[Bank]:
    """All registered banks, reloading when banks.json changed on disk."""
    global _cache, _cache_sig, _doc_extra
    with _lock:
        path = banks_file()
        sig = _signature(path)
        if not force and _cache is not None and _cache_sig == sig:
            return list(_cache)
        banks, extra = _read(path)
        _cache, _cache_sig, _doc_extra = banks, sig, extra
        return list(banks)


def save(banks: list[Bank]) -> None:
    """Write the registry atomically (tmp + ``os.replace``)."""
    global _cache, _cache_sig
    with _lock:
        path = banks_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        doc: dict[str, Any] = dict(_doc_extra)
        doc["version"] = REGISTRY_VERSION
        doc["banks"] = [b.to_json() for b in banks]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # The registry carries per-bank tokens, so it is now a secret-bearing
        # file and gets `api.token`'s treatment. Set on the temp file, before
        # the rename, so the document is never readable-by-all even briefly.
        # POSIX-only in effect: on Windows this only clears the read-only bit,
        # and the real protection is the user-profile ACL.
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        _cache = list(banks)
        _cache_sig = _signature(path)


# ------------------------------------------------------------------ lookup


def list_banks() -> list[Bank]:
    return load()


def get(bank_id: str) -> Bank:
    for bank in load():
        if bank.id == bank_id:
            return bank
    raise BankNotFound(f"no bank with id {bank_id!r}")


def unique_name(
    candidate: str,
    *,
    banks: list[Bank] | None = None,
    exclude_id: str | None = None,
) -> str:
    """``candidate`` if free, else ``candidate-2``, ``candidate-3``, …"""
    pool = load() if banks is None else banks
    taken = {
        _norm_name(b.name) for b in pool if exclude_id is None or b.id != exclude_id
    }
    base = candidate.strip() or "bank"
    if _norm_name(base) not in taken:
        return base
    n = 2
    while _norm_name(f"{base}-{n}") in taken:
        n += 1
    return f"{base}-{n}"


def resolve(ref: str) -> Bank:
    """Find a bank by id, by name, or by a path (§6.4).

    The path form is what a bare CLI invocation and the auto-inject hook both
    use: they know only a ``cwd``. It looks in **both directions**, and the
    order matters.

    *Upwards first* — the deepest bank whose root is an ancestor of the path.
    A path inside a bank's memory is that bank's, and a nested bank is more
    specific than the one enclosing it.

    *Then downwards* — a bank whose root lies **under** the path. Without this
    the canonical layout defeats the default: memory lives at
    ``<project>/.claude/memory``, so running ``mnemo search`` from the project
    root — the obvious place to run it — matched nothing and exited 1, while
    the same command two directories down worked. Ambiguity is refused rather
    than guessed: a path containing several banks (a folder full of projects)
    raises instead of silently picking one.
    """
    ref = (ref or "").strip()
    if not ref:
        raise BankNotFound("empty bank reference")
    banks = load()

    if _HEX16.match(ref):
        for bank in banks:
            if bank.id == ref:
                return bank

    wanted = _norm_name(ref)
    by_name = [b for b in banks if _norm_name(b.name) == wanted]
    if len(by_name) > 1:
        raise AmbiguousBankRef(
            f"{len(by_name)} banks are named {ref!r} — fix banks.json"
        )
    if by_name:
        return by_name[0]

    # Only an ABSOLUTE path is a path here, and that is a contract, not an
    # oversight: this runs inside the service, whose cwd is its own and has
    # nothing to do with the caller's. Joining a relative ref to *this*
    # process's cwd would answer confidently about a folder the user never
    # named. Making a relative ref absolute is the client's job, done once, in
    # `cli._bank_ref`.
    path = Path(ref).expanduser()
    if path.is_absolute():
        # Disabled banks are invisible to the PATH form only. A path has to
        # route somewhere useful: if a nested bank is switched off, the
        # enclosing bank still holds that file's memory and must win, or
        # disabling a child silently blinds the parent for that subtree. An
        # explicit id or name still resolves a disabled bank — otherwise there
        # would be no way to address one in order to re-enable it.
        #
        # A **frozen** bank stays visible here: it is still searchable, so a
        # path inside it must keep routing to it. Handing that path to the
        # enclosing bank instead would answer out of a different bank's
        # memory — quietly, and with no way for the caller to tell.
        target = _canonical(path)
        live = [b for b in banks if b.searchable]

        best: Bank | None = None
        best_len = -1
        for bank in live:
            root = _canonical(bank.root)
            if target == root:
                return bank
            if target.startswith(root.rstrip("/") + "/") and len(root) > best_len:
                best, best_len = bank, len(root)
        if best is not None:
            return best

        # Nothing encloses the path — look for banks it contains.
        prefix = target.rstrip("/") + "/"
        inside = [b for b in live if _canonical(b.root).startswith(prefix)]
        if len(inside) == 1:
            return inside[0]
        if len(inside) > 1:
            names = ", ".join(sorted(b.name for b in inside))
            raise AmbiguousBankRef(
                f"{len(inside)} banks live under {ref!r} ({names}) — "
                f"name one with --bank"
            )

    raise BankNotFound(f"no bank matches {ref!r}")


# ------------------------------------------------------------------ mutate


def add(
    root: Path | str,
    *,
    name: str | None = None,
    provider: str | None = None,
) -> Bank:
    """Register a root. Returns the stored bank — read its ``name`` back,
    it may carry a uniqueness suffix."""
    with _lock, _file_lock():
        resolved = Path(root).expanduser().resolve()
        if not resolved.is_dir():
            raise NotADirectoryError(f"{resolved} is not a directory")
        # Re-read inside the lock: another process may have registered a bank
        # since our cached copy, and appending to a stale list would drop it.
        banks = load(force=True)
        bid = derive_bank_id(resolved)
        for bank in banks:
            if bank.id == bid:
                raise BankExists(
                    f"{resolved.as_posix()} is already registered as {bank.name!r}"
                )
        chosen = unique_name(name or default_name(resolved), banks=banks)
        bank = Bank(
            id=bid,
            name=chosen,
            root=resolved,
            provider=provider,
            state=STATE_ENABLED,
            exclude=_default_exclude(),
            added_at=_now_iso(),
            token=new_token(),
        )
        banks.append(bank)
        save(banks)
        return bank


# ------------------------------------------------------------------ tokens


def ensure_tokens() -> int:
    """Give every bank a token, keeping everything else in the file intact.

    A **migration, not a rewrite**: banks registered before per-bank tokens
    existed get one added; every other field — including anything a human put
    there that this module does not understand — is round-tripped by
    `to_json` / `extra`. Returns how many tokens were minted, so a caller can
    say nothing when there was nothing to do.

    Called once at service start (§9.6). Not from `load`: that runs on every
    request path and must never write.
    """
    with _lock, _file_lock():
        banks = load(force=True)
        minted = 0
        for i, bank in enumerate(banks):
            if bank.token:
                continue
            banks[i] = _with_token(bank, new_token())
            minted += 1
        if minted:
            save(banks)
            log.info("minted a token for %d bank(s) with none", minted)
        return minted


def token_for(bank_id: str) -> str:
    """This bank's token, minted and persisted if it has none."""
    bank = get(bank_id)
    if bank.token:
        return bank.token
    return regenerate_token(bank_id)


def resolve_by_token(token: str) -> Bank | None:
    """The bank a token addresses, or ``None``.

    **This is the whole of bank addressing on the plain MCP face.** Once a
    token belongs to a bank, the token *is* the address: there is no URL
    segment to parse, no header to trust and no "if there is only one bank"
    guess. One input, one answer.

    A linear scan with ``compare_digest`` rather than a token→bank dict. The
    dict would be O(1) and would need its own cache keyed on the registry's
    mtime — a second invalidation path whose failure mode is serving the wrong
    bank. The scan is one 48-character comparison per bank on an already-warm
    ``load()`` (~40 us total for this machine's registry, against ~200 ms for
    the search behind it), and it compares in constant time, which a dict
    lookup does not. Simplicity wins on both axes here.

    Returns ``None`` rather than raising: the only caller is authentication,
    where "no match" is a 401 and not an exceptional condition. A disabled
    bank still authenticates — the token is genuine — and the tool then says
    the bank is disabled, which is a far more useful answer than a 401 the
    holder cannot distinguish from a wrong credential.
    """
    candidate = (token or "").strip()
    if not candidate:
        return None
    for bank in load():
        if bank.token and secrets.compare_digest(candidate, bank.token):
            return bank
    return None


def regenerate_token(bank_id: str) -> str:
    """Mint a fresh token for one bank. The old one stops working at once.

    Every `.mcp.json` pointed at this bank has to be re-issued afterwards —
    which is the point of the button in the console that calls this.
    """
    with _lock, _file_lock():
        banks = load(force=True)
        idx = next((i for i, b in enumerate(banks) if b.id == bank_id), None)
        if idx is None:
            raise BankNotFound(f"no bank with id {bank_id!r}")
        token = new_token()
        banks[idx] = _with_token(banks[idx], token)
        save(banks)
        return token


def _with_token(bank: Bank, token: str) -> Bank:
    """`Bank` is frozen — this is the one place a token is swapped in."""
    return Bank(
        id=bank.id,
        name=bank.name,
        root=bank.root,
        provider=bank.provider,
        state=bank.state,
        exclude=list(bank.exclude),
        added_at=bank.added_at,
        token=token,
        extra=bank.extra,
    )


def remove(bank_id: str, *, drop_index: bool = True) -> None:
    """Unregister a bank. ``.md`` files are never touched — only the index."""
    with _lock, _file_lock():
        banks = load(force=True)
        keep = [b for b in banks if b.id != bank_id]
        if len(keep) == len(banks):
            raise BankNotFound(f"no bank with id {bank_id!r}")
        save(keep)
        if drop_index:
            db = state_dir() / f"{bank_id}.db"
            for suffix in ("", "-wal", "-shm"):
                candidate = db.with_name(db.name + suffix)
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass


def update(bank_id: str, **fields: Any) -> Bank:
    """Change ``name`` / ``provider`` / ``state`` / ``exclude`` of a bank.

    ``root`` is not updatable: the id is derived from it, so moving a root is
    a remove + add, not an edit that silently orphans a database.

    ``enabled`` is accepted as a deprecated alias for ``state`` so an older
    caller keeps working, and is translated the same way the registry file's
    boolean is. Passing both is refused rather than resolved by precedence:
    the two can disagree, and guessing which one the caller meant is exactly
    the failure this field replaced.
    """
    with _lock, _file_lock():
        banks = load(force=True)
        idx = next((i for i, b in enumerate(banks) if b.id == bank_id), None)
        if idx is None:
            raise BankNotFound(f"no bank with id {bank_id!r}")
        current = banks[idx]

        unknown = set(fields) - {"name", "provider", "state", "enabled", "exclude"}
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        if "state" in fields and "enabled" in fields:
            raise ValueError("pass 'state' or 'enabled', not both")

        state = current.state
        if "state" in fields:
            state = str(fields["state"]).strip().lower()
            if state not in STATES:
                raise ValueError(
                    f"unknown state {fields['state']!r} — "
                    f"expected one of {', '.join(STATES)}"
                )
        elif "enabled" in fields:
            state = STATE_ENABLED if bool(fields["enabled"]) else STATE_DISABLED

        name = current.name
        if "name" in fields:
            name = str(fields["name"]).strip()
            if not name:
                raise ValueError("name cannot be empty")
            clash = [
                b
                for b in banks
                if b.id != bank_id and _norm_name(b.name) == _norm_name(name)
            ]
            if clash:
                # Deliberately not auto-suffixed: a rename is an intentional
                # act, and quietly renaming to something else would break the
                # .mcp.json that points at the requested name.
                raise BankExists(f"another bank is already named {name!r}")

        updated = Bank(
            id=current.id,
            name=name,
            root=current.root,
            provider=fields.get("provider", current.provider),
            state=state,
            exclude=list(fields.get("exclude", current.exclude)),
            added_at=current.added_at,
            # Carried, never regenerated by an edit: renaming a bank must not
            # silently invalidate every `.mcp.json` pointing at it. Rotation
            # is `regenerate_token`, and only that.
            token=current.token,
            extra=current.extra,
        )
        banks[idx] = updated
        save(banks)
        return updated


# ----------------------------------------------------------- orphaned indexes
#
# An index is named ``sha1(canonical root)[:16].db``, so a bank finds its file
# by derivation and nothing links the file back to a bank. That is what makes
# a bank's index unambiguous, and it is also why files are left behind: drop
# the registry entry and the derivation has no one left to run.
#
# Three ways it happens, all normal:
#   * a bank unregistered with ``drop_index=False``;
#   * a hand-edited ``root`` in banks.json — the id is re-derived from the new
#     root on purpose (``_from_json``), and the old file is the price;
#   * v2 keyed an index by the *project* root, v3 by the *bank* root, so every
#     v2 index orphaned itself the moment v3 first ran.
#
# Nothing scans this directory during normal operation, so an orphan is inert:
# never opened, never searched, never listed. It only costs disk. Cleaning is
# therefore a deliberate act (`mnemo clean-orphans`), never a startup chore —
# an index that is wrongly deleted costs a full rebuild to get back.

# ``service.db`` is the journal, not a bank index. Excluded by name because
# deriving "not a bank" from its content would be one inference away from
# deleting the service's own log.
_NON_BANK_DB = frozenset({"service"})


@dataclass(frozen=True)
class OrphanIndex:
    """An index file in ``state/`` that no registered bank claims."""

    path: Path
    id: str
    size: int                  # the .db plus its -wal / -shm siblings
    root: str | None           # root recorded in ``meta``; None before v3
    root_exists: bool          # ...and whether that root is still on disk
    schema: str | None         # ``schema_version``; None means "pre-meta"
    files: int | None          # indexed files, when the table is readable
    last_indexed: str | None
    error: str | None          # set when the file could not be read at all


def _index_size(path: Path) -> int:
    """The database plus its WAL/SHM siblings — what deleting it frees."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        with contextlib.suppress(OSError):
            total += path.with_name(path.name + suffix).stat().st_size
    return total


def _inspect_index(path: Path) -> OrphanIndex:
    from . import store

    probed = store.probe(path)
    meta = probed["meta"]
    root = meta.get("bank_root") or None
    return OrphanIndex(
        path=path,
        id=path.stem,
        size=_index_size(path),
        root=root,
        root_exists=bool(root) and Path(root).is_dir(),
        schema=meta.get("schema_version"),
        files=probed["files"],
        last_indexed=meta.get("last_indexed_at"),
        error=probed["error"],
    )


def orphan_indexes() -> list[OrphanIndex]:
    """Every index file in ``state/`` that belongs to no registered bank.

    The registry is read first and **its failure is allowed to propagate**.
    Treating an unreadable ``banks.json`` as "no banks" would report every
    live index as an orphan — and that is the one mistake here the ``.md``
    cannot undo cheaply, since agreeing to the list would delete indexes that
    then cost a full rebuild.
    """
    live = {bank.id for bank in load(force=True)}
    return [
        _inspect_index(path)
        for path in sorted(state_dir().glob("*.db"))
        if path.stem not in _NON_BANK_DB and path.stem not in live
    ]


def delete_index(index_id: str) -> tuple[int, list[Path]]:
    """Delete one orphaned index and its siblings. Returns ``(removed, failed)``.

    The registry is re-read here rather than trusted from the caller's earlier
    listing: between listing and confirming, a bank may have been registered
    on exactly this root — by the console, by another session, by `init`. A
    stale list must not be able to delete a live index.
    """
    if index_id in _NON_BANK_DB:
        raise ValueError(f"{index_id}.db is not a bank index")
    if index_id in {bank.id for bank in load(force=True)}:
        raise ValueError(f"{index_id} belongs to a registered bank")

    base = state_dir() / f"{index_id}.db"
    removed, failed = 0, []
    for suffix in ("", "-wal", "-shm"):
        candidate = base.with_name(base.name + suffix)
        try:
            candidate.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            failed.append(candidate)
    return removed, failed
