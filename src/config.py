"""Central configuration — bank-agnostic.

The engine is installed once at user scope and serves ANY bank: the bank
root is passed in (CLI --root, default cwd). A bank is one root folder,
flat — every ``.md`` under it belongs to the same index (v3: no internal
scopes). Per-bank index DBs and the shared model cache live under the
user-scope home.

**Sectioned by owner (Memory-contracts-v3 §1.1).** This is the one file every
role needs, and a second config module would be a parallel path. So each
section below carries an owner banner: edit only your own section, and append
new keys at the end of it. The section order is fixed — do not reorder, and do
not delete a banner that is still empty.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

# --- paths & state ------------------------------  engine-dev

# User-scope home: installed once, shared by all banks.
USER_HOME: Path = Path(
    os.environ.get("MNEMO_HOME", Path.home() / ".claude" / "mnemo")
)
# Per-bank index DBs. $MNEMO_STATE_DIR relocates just the *writable* state
# (index + logs + token) without moving the engine or model-cache: a container
# mounts the engine + model-cache read-only from the host and points this at
# its own ephemeral filesystem, so the index dies with the container and never
# litters the host. Unset -> user-scope default, identical to before.
STATE_DIR: Path = Path(
    os.environ.get("MNEMO_STATE_DIR", USER_HOME / "state")
)
MODEL_CACHE: Path = USER_HOME / "model-cache"  # e5-large, once for all banks

# Whether two paths differing only in case are the same path. NTFS says yes —
# ``rglob("*.md")`` there happily returns ``NOTES.MD``, and a user typing
# ``notes`` or an exclude pattern of ``.venv/**`` means it whatever the case on
# disk. POSIX says no, and folding would be wrong. Both the exclude walk and
# the search path_prefix read this, so they cannot drift apart.
FOLD_PATH_CASE: bool = os.name == "nt"


@dataclass(frozen=True)
class BankPaths:
    """Resolved locations for one bank root."""

    id: str    # sha1-derived bank id (see bank_id)
    root: Path
    db: Path   # user-scope state/<bank_id>.db


def bank_id(root: Path) -> str:
    """Stable id for a bank root — also the name of its index file.

    Same ``sha1(root)[:16]`` scheme as v2, with two canonicalisations so one
    folder never ends up with two databases: ``as_posix()`` (``E:\\x`` and
    ``E:/x`` are the same bank) and lowercasing on Windows (NTFS is
    case-insensitive; POSIX paths are case-sensitive and stay untouched).
    Kept here rather than in the registry so both agree by construction.
    """
    canonical = root.expanduser().resolve().as_posix()
    if os.name == "nt":
        canonical = canonical.lower()
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]


def resolve(root: Path | str | None) -> BankPaths:
    """Resolve all paths for a bank root.

    Precedence: explicit arg > $MNEMO_ROOT > $CLAUDE_PROJECT_DIR > cwd.
    Claude Code supplies CLAUDE_PROJECT_DIR to project-scoped MCP servers
    and hooks, so resolution never depends on the child cwd.

    The key stays ``sha1(root)``, but the root it hashes is now the bank root
    — so v2 index files are simply not reused (they are also incompatible;
    ``store`` drops them).
    """
    chosen = (
        root
        or os.environ.get("MNEMO_ROOT")
        or os.environ.get("CLAUDE_PROJECT_DIR")
    )
    root_path = Path(chosen).resolve() if chosen else Path.cwd().resolve()
    bid = bank_id(root_path)
    return BankPaths(id=bid, root=root_path, db=STATE_DIR / f"{bid}.db")


# --- embedding model & daemon (A) ---------------  engine-dev

# Embedding model. Decision was multilingual-e5-base; fastembed 0.8.0 ships
# no e5-base, so e5-large (same family, 1024-dim) is the documented fallback.
EMBEDDING_MODEL: str = "intfloat/multilingual-e5-large"
EMBEDDING_DIM: int = 1024

# Warm embedding helper (resident model holder). TCP — NOT a unix socket:
# CPython does not expose socket.AF_UNIX on Windows, and we need
# Linux/macOS/Windows parity with zero OS-specific quirks.
#
# Two addresses, two roles: $MNEMO_EMBED_BIND is what the resident listens
# on, $MNEMO_EMBED_HOST is what a client dials. Both default to loopback,
# so the base mode is unchanged: one resident per machine, nothing on the
# network. Widening the bind lets isolated environments on the same machine
# (e.g. containers reaching the host over its bridge address) share a single
# resident and a single copy of the model in RAM. Exposing the resident
# beyond the machine is a deployment decision, guarded outside this process.
EMBED_BIND: str = os.environ.get("MNEMO_EMBED_BIND", "127.0.0.1")
EMBED_HOST: str = os.environ.get("MNEMO_EMBED_HOST", "127.0.0.1")
EMBED_PORT: int = int(os.environ.get("MNEMO_EMBED_PORT", "8917"))
EMBED_TOKEN_FILE: Path = STATE_DIR / "embed.token"

# A client may only autostart a resident it can actually own: one reachable
# on this machine's loopback. Pointed at a remote resident, the client uses
# it as-is and degrades gracefully when it is down — spawning a shadow copy
# of the model next to it would silently undo the whole point of sharing one.
_LOOPBACK: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})
EMBED_HOST_IS_LOCAL: bool = EMBED_HOST in _LOOPBACK

# How long to wait for the resident to accept a connection. This is a
# liveness probe, not a request: measured on loopback, a live resident
# accepts in ~0.5 ms, and a dead port refuses rather than dawdling. The old
# 0.5 s was a thousandfold margin that every degraded search paid in full.
# The knob exists for a resident so saturated that its listen backlog is
# full — if that is ever observed, raise this rather than re-argue it.
# A remote resident keeps the generous value: the measurement was loopback.
EMBED_PROBE_TIMEOUT: float = float(
    os.environ.get("MNEMO_EMBED_PROBE_TIMEOUT", "0.15")
)

# Idle exit frees the ~1.6 GB the resident holds. 0 disables it: a machine
# serving isolated environments keeps the model resident permanently.
#
# COST OF THE CURRENT DEFAULT, measured: once the resident has exited, the
# next search pays ~9 s — 0.5 s failed probe + 2.3 s to spawn and bind +
# 6.2 s to load the model. So with 1800 s, the first search after any
# half-hour gap takes ~9 s, which sits badly against FR-3 ("пошук —
# миттєвий").
#
# It stays at 1800 anyway until phase 5, and deliberately: `0` means the
# model is pinned in RAM with no supported way to release it until
# `mnemo service stop` exists. Phase 5 owns both that command and the
# always-on service that keeps the resident warm, and flips this to 0.
EMBED_IDLE_TIMEOUT: int = int(os.environ.get("MNEMO_EMBED_IDLE_TIMEOUT", "1800"))

# Embedding CPU cap. ONNX Runtime defaults to ALL cores per embed call;
# the serial resident under multi-agent load then pegs the whole machine.
# Bound every embed (resident, in-process fallback, ingest, tests, warmup)
# to a fraction of the *available* CPUs. sched_getaffinity honours
# cgroup/taskset limits on Linux; cpu_count is the cross-platform fallback.
# MNEMO_EMBED_THREADS overrides the computed value explicitly.
#
# v3 raises the ceiling from cpu/3 to cpu*3/4: indexing is the throughput
# bottleneck and the old third-of-the-machine cap left most cores idle. ONNX
# scales sub-linearly past the physical cores, so this is not a 2.25x win —
# ≈1.5x on a 12-CPU machine, small fixture; re-measure on a real bank.
EMBED_THREADS_FRACTION: tuple[int, int] = (3, 4)


def _available_cpus() -> int:
    try:
        return len(os.sched_getaffinity(0))  # Linux: respects cgroup/taskset
    except AttributeError:
        return os.cpu_count() or 1           # Windows/macOS fallback


def _embed_threads() -> int:
    cpu = _available_cpus()
    override = os.environ.get("MNEMO_EMBED_THREADS")
    if override:
        try:
            return max(1, min(int(override), cpu))
        except ValueError:
            pass  # garbage env -> fall through to the computed default
    num, den = EMBED_THREADS_FRACTION
    return max(1, cpu * num // den)


EMBED_THREADS: int = _embed_threads()


# --- chunking & search knobs --------------------  engine-dev

# Chunking: heading-aware (characters); a small file becomes one whole chunk.
CHUNK_CAPACITY: tuple[int, int] = (200, 1200)

# Search knobs.
TOP_K: int = 5
RRF_K: int = 60
# Neighbor expansion: pull ±N adjacent chunks from the same file around
# each hit and merge overlapping windows into one block. Wider context
# helps both the auto-inject path (model has a head start) and agent /
# CLI searches (one query returns a self-contained excerpt). 0 = off.
NEIGHBOR_WINDOW: int = 1

# Auto-inject (UserPromptSubmit) — how many sections to surface.
INJECT_TOP_N: int = 3
# Soft wall-clock budget for one inject / MCP search call (seconds).
# Sits below Claude Code's 30 s hook timeout so we exit gracefully with a
# log line instead of being SIGKILL-ed mid-step. Covers embed + search.
INJECT_BUDGET_S: float = 20.0

# Inject telemetry log — JSONL, user-scope, rotated, ONE FILE PER BANK.
# Path: ``state/logs/<bank_id>.log`` — same id as the bank's index DB so a
# log line and its index sit side by side. One line per hook-inject call
# (ok / skipped / errored) so MIN_SIM / TOP_N / gate behaviour is tunable
# from real data instead of guesswork. Replaced by the service log
# (``service.db``) in phase 2.
INJECT_LOG_DIR: Path = STATE_DIR / "logs"
INJECT_LOG_MAX_BYTES: int = 5 * 1024 * 1024   # 5 MB per file
INJECT_LOG_BACKUPS: int = 3                   # 3 rotated → ~20 MB cap per bank
INJECT_LOG_PROMPT_CHARS: int = 200            # truncate prompt in the log
# Weak-match gate (auto-inject path only; manual search is never gated).
# Cosine-similarity floor on the vector leg + a minimum query length.
# e5 has a high baseline similarity (anisotropy); measured on the test
# corpus: relevant top hits ~0.84-0.87, junk/off-topic ~0.78-0.81. 0.83
# keeps every relevant top-1 and cuts all junk — but the margin is narrow
# (~0.03), so this is PROVISIONAL: recalibrate on real prompts at the pilot.
MIN_SIM: float = 0.83
MIN_QUERY_CHARS: int = 8

# Indexer knobs (block D). Kept in this engine-dev section rather than under
# a tenth banner, so the section order stays exactly as §1.1 pins it.
#
# Directories that never hold curated memory but are full of markdown. A bank
# pointed at a project root (rather than at its memory folder) would otherwise
# index a virtualenv's third-party docs and drown the real notes. The registry
# reads this same list as its per-bank default (registry._default_exclude), so
# the walk and the registry cannot disagree. ``venv`` appears without the dot
# as well: the plain name is at least as common as ``.venv``.
DEFAULT_EXCLUDE: list[str] = [
    ".git/**",
    ".venv/**",
    "venv/**",
    "node_modules/**",
    "__pycache__/**",
]

# Chunks per embed call and per commit. Small enough that one batch cannot
# hit a timeout and that an urgent edit waits at most one batch; large enough
# that per-call overhead stays negligible.
BATCH_SIZE: int = max(1, int(os.environ.get("MNEMO_BATCH_SIZE", "16")))

# Ceiling for serving one file's raw text over the API (phase 6 viewer).
FILE_MAX_BYTES: int = int(os.environ.get("MNEMO_FILE_MAX_BYTES", str(2 * 1024 * 1024)))


# --- providers (B) ------------------------------  engine-dev

# Embedding provider (see src/providers/). `local` = the resident ONNX
# e5-large; `api` = an external embeddings endpoint (added in a later phase).
# The provider key (name:model:dim) is recorded in each bank's index, so a
# provider change is detected and the bank is rebuilt instead of mixing
# vectors from two different models in one database.
EMBED_PROVIDER: str = os.environ.get("MNEMO_PROVIDER", "local")


# --- registry & banks (G) -----------------------  service-dev
# (empty — add MNEMO_BANKS_FILE and friends here)


# --- api / websocket (J) ------------------------  service-dev
# (empty — add MNEMO_API_HOST / _PORT / _TOKEN / _URL here)


# --- queue & watcher (E, F) ---------------------  service-dev
# (empty — add MNEMO_QUEUE_PRIORITY / _WORKERS / _DEBOUNCE_MS here)


# --- service log & retention (I) ----------------  service-dev
# (empty — add MNEMO_LOG_RETENTION_DAYS / _MAX_ROWS here)


# --- process lifecycle (L) ----------------------  platform-dev

# Process-state file (contracts §11.2). Owned by the backend: it publishes
# pid/host/port/version here on startup and removes the file on a clean exit.
SERVICE_INFO_FILE: Path = STATE_DIR / "service.json"

# Spawn-identity file, owned by service_ctl. Deliberately NOT service.json:
# the backend rewrites that file wholesale, which would erase the one field
# that makes liveness trustworthy. A bare PID is not proof the process is
# ours (the OS reuses PIDs), so this records the process creation time as
# well — see service_ctl.process_fingerprint.
SERVICE_PID_FILE: Path = STATE_DIR / "service.pid"

# How long `service stop` waits for a graceful exit before escalating.
SERVICE_STOP_TIMEOUT: float = float(
    os.environ.get("MNEMO_SERVICE_STOP_TIMEOUT", "10.0")
)

# How long `service start` waits for the child to still be alive before
# calling the spawn a success. Short: this only catches an immediate crash
# (bad interpreter, import error), not a slow bind.
SERVICE_START_GRACE: float = float(
    os.environ.get("MNEMO_SERVICE_START_GRACE", "1.5")
)
