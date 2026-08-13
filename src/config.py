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


# --- environment file (preamble, owned by nobody's section) -------------
#
# Every knob below reads ``os.environ``. That works for a hand-run command,
# which inherits the shell — and silently does NOT work for the service the
# installer registers, which is the process that reads most of them:
#
# * ``systemd --user`` never sources ``~/.profile``, and the unit we write
#   carries neither ``Environment=`` nor ``EnvironmentFile=``;
# * a launchd LaunchAgent likewise starts with a bare environment.
#
# So on Linux and macOS every ``MNEMO_*`` override was invisible to the
# autostarted backend — set it, restart, nothing changes, no error. (Windows
# escaped by accident: the installer writes a *user-scope* variable and the
# logon task inherits it.)
#
# The fix is deliberately not a second per-OS unit template. One optional
# file next to the rest of the writable state is read here, before any knob
# is evaluated, so systemd, launchd, Task Scheduler and a plain shell all see
# the same values. Real environment WINS over the file: exporting a variable
# for one run must still beat the stored default.
#
# Format: ``KEY=value`` per line, ``#`` comments, blank lines ignored.
# Optional by design — its absence is the normal case and is not an error.
ENV_FILE_NAME = "mnemo.env"


def _load_env_file() -> Path | None:
    """Merge ``<state>/mnemo.env`` into ``os.environ`` without overriding it.

    Resolved from the *real* environment only (``MNEMO_STATE_DIR`` /
    ``MNEMO_HOME``), because the constants that would normally answer this
    are defined below and do not exist yet.
    """
    state = os.environ.get("MNEMO_STATE_DIR")
    path = Path(state) if state else Path(
        os.environ.get("MNEMO_HOME", Path.home() / ".claude" / "mnemo")
    ) / "state"
    path = path / ENV_FILE_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # A value may be quoted the way a shell would quote it; anything
        # else is taken literally, spaces included.
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
    return path


ENV_FILE: Path | None = _load_env_file()


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

# The resident's shared secret lives with the ENGINE, not with the state.
#
# It used to sit in STATE_DIR, and that was a design error with an expensive
# symptom. The resident is one per machine; STATE_DIR is per-context and is
# explicitly meant to be relocated (see its docstring — a container points it
# at ephemeral storage). So every process that moved STATE_DIR minted its own
# token, the shared resident rejected it, and the client "degraded" by loading
# 2.2 GB in-process and embedding there — 50x slower, no error, no log line.
# Deriving a machine-shared secret from a per-context path guarantees that.
#
# MNEMO_EMBED_TOKEN_FILE overrides it for the genuinely separate case: a
# second resident that must not share this machine's secret.
EMBED_TOKEN_FILE: Path = Path(
    os.environ.get("MNEMO_EMBED_TOKEN_FILE", USER_HOME / "embed.token")
)
# Where the token used to live. Read once, to adopt it silently, so upgrading
# does not orphan a resident that is already running with the old secret.
LEGACY_EMBED_TOKEN_FILE: Path = USER_HOME / "state" / "embed.token"

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

# How long to wait for a BATCH of passages to come back. Scales with the
# batch, because the wait is a CPU-bound embed whose duration depends on the
# batch size and on how busy the machine is — a fixed ceiling is the wrong
# shape for that, and the old fixed 60 s was measured being crossed: a
# 16-chunk batch at 4 threads took 61.7 s. When it trips, the client gives up
# on a resident that is merely slow and the caller loads its own ~2.2 GB copy
# — so the cost of being too tight is far worse than the cost of waiting.
#
# Indexing is background work behind the queue; nothing blocks a session on
# it, so a generous budget costs nothing real. The floor keeps small batches
# from getting an absurdly short one. A ceiling still exists so a genuinely
# wedged resident cannot pin a worker forever.
EMBED_SECONDS_PER_CHUNK: float = float(
    os.environ.get("MNEMO_EMBED_SECONDS_PER_CHUNK", "15")
)
EMBED_BATCH_TIMEOUT_FLOOR: float = 60.0

# Idle exit for the resident. 0 = never exit on idle, which is the v3 default
# (design §4: the model stays warm).
#
# `0` does NOT mean "always resident". The resident is still started on demand
# by the next search or hook, and it is released by `mnemo service stop`,
# which reaps it along with the backend. So the memory is recoverable by a
# command the user already knows; it simply is not surrendered on a timer.
#
# WHY THE TIMER LOST, measured: once the resident has exited, the next search
# pays ~9 s — 0.5 s failed probe + 2.3 s to spawn and bind + 6.2 s to load
# e5-large. With the old 1800 s, the first search after any half-hour gap
# cost that, against FR-3's "пошук — миттєвий". Paying 9 s repeatedly to
# reclaim 1.6 GB that a single command can reclaim on purpose is the wrong
# trade on a developer machine.
#
# This default waited on `service stop` genuinely reaping the *resident* —
# not just the backend, which is a different process holding no model.
# Verified before flipping: a live resident at 1501 MB, `stop_resident()` ->
# stopped in 0.5 s, nothing left listening on the port, process gone.
# Set MNEMO_EMBED_IDLE_TIMEOUT=1800 to restore the old behaviour.
EMBED_IDLE_TIMEOUT: int = int(os.environ.get("MNEMO_EMBED_IDLE_TIMEOUT", "0"))

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

# Chunking: heading-aware; a small file becomes one whole chunk.
#
# Characters, and they stay characters. Splitting by TOKENS was built and
# measured on the real bank (tests/eval_search.py, 64 labeled queries) and it
# retrieves WORSE at the same median chunk size: 0.50 MRR against 0.53, and
# 0.49 with a higher ceiling. The token rule is the more principled-looking
# one and the evidence went the other way, so the metric stays.
CHUNK_CAPACITY: tuple[int, int] = (200, 1200)
# Below this, a chunk is folded into the next one. It is the bare-heading
# case: `## Logs` alone is a chunk the splitter is right to cut and search is
# wrong to return, because it wins both retrieval legs at once and displaces
# the section it names. Measured neutral on retrieval — it is here because a
# bare heading is never the answer, not because the metrics asked for it.
CHUNK_MERGE_FLOOR_CHARS: int = 150
# The one thing characters cannot promise: that a chunk fits the model's
# context window. e5-large truncates at 512 tokens INCLUDING two specials,
# and truncation is silent — the tail simply is not in the index. A chunk
# over this is re-split; on text that never reaches it (this bank peaks at
# 469) nothing moves at all.
CHUNK_TOKEN_CEILING: int = 480

# Search knobs.
TOP_K: int = 5
RRF_K: int = 60
# The two retrieval legs are NOT equally good, so they do not get an equal
# vote. Measured on the real bank with `tests/eval_search.py` (64 labeled
# queries): the vector leg alone scores 0.51 MRR / 0.75 recall@10, the
# lexical leg 0.32 / 0.61, and fusing them 1:1 lands at 0.49 / 0.75 —
# BELOW the vector leg alone. Weighting it 4:1 through 12:1 gives a flat
# 0.54 / 0.81, better than either leg on all six metrics.
#
# It is a plateau rather than a peak, so the exact number is not load-
# bearing; 6 sits in the middle of it. The mechanism: at this weight the
# lexical leg can no longer outrank the vector ordering, it can only lift a
# chunk that BOTH legs found — which is the job it is actually good at.
RRF_VECTOR_WEIGHT: float = 6.0
# Neighbor expansion: pull ±N adjacent chunks from the same file around
# each hit and merge overlapping windows into one block, so one query
# returns a self-contained excerpt instead of a fragment. 0 = off.
NEIGHBOR_WINDOW: int = 1

# `INJECT_TOP_N` is gone with the auto-inject hook itself (design #27). The
# JSONL inject telemetry (`INJECT_LOG_*`) and `INJECT_BUDGET_S` went earlier,
# with `inject_log.py`: the service journal (`service.db`, §7.5) records the
# same calls as ordinary `query_events` and each hit carries its `sim` in
# `hits_json`, which is what that telemetry existed to expose.
#
# Weak-match gate, opt-in per call — `search.py` gates only when asked, and
# the manual/tool path never asks. Cosine floor on the vector leg + a minimum
# query length.
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

# Chunks per embed call and per commit — now a CEILING, not the batch size.
# Small enough that one batch cannot hit a timeout and that an urgent edit
# waits at most one batch; large enough that per-call overhead stays
# negligible.
BATCH_SIZE: int = max(1, int(os.environ.get("MNEMO_BATCH_SIZE", "16")))

# The other half of the batching rule, and the one that actually bounds cost.
#
# A batch is padded to its longest member, so it costs `longest x count`, not
# `count`. `index.plan_batches` therefore sorts a file's chunks by length and
# cuts a batch when `longest x count` would exceed this budget, with
# BATCH_SIZE as a backstop in items. Characters, not tokens, because that is
# what a chunk can be measured in before a tokenizer exists (see chunker: a
# machine that never ran `warmup` cannot count tokens) — and the rule only
# needs a monotone proxy, not a unit.
#
# 1200 is measured, on this bank, interleaved and best-of-2: 1.38x against
# the 19200 that matches the old fixed-16 rule. Combined with the sort, 1.6x
# on a full rebuild.
#
# **This value is for the CPU resident and is wrong for a GPU** — the same
# sweep against Ollama scored 0.50x here. It is not the shared default for
# that reason: `EmbeddingProvider.pad_budget` carries the conservative 19200
# and `local` overrides it with this. Setting MNEMO_BATCH_PAD_BUDGET moves
# the local one only.
#
# Not lowered further although 600 measured marginally faster (1.15x against
# 2400, versus 1200's 1.08x): those last steps sit inside the machine's own
# drift — the same arm varied 20% between rounds — and a batch is the commit
# unit, so 600 means one transaction per chunk. Single digits of embedding
# time is not worth an order of magnitude more fsyncs.
BATCH_PAD_BUDGET: int = max(
    1, int(os.environ.get("MNEMO_BATCH_PAD_BUDGET", "1200"))
)

# Ceiling for serving one file's raw text over the API (phase 6 viewer).
FILE_MAX_BYTES: int = int(os.environ.get("MNEMO_FILE_MAX_BYTES", str(2 * 1024 * 1024)))


# --- providers (B) ------------------------------  engine-dev

# Which provider, and how to reach an external one, are NOT constants here.
#
# They are the settings the cabinet edits, and this module evaluates every
# knob once at import — so a constant would serve whatever the process started
# with, forever, no matter what was saved. That is the same frozen-path scar
# as `BANKS_FILE` two sections down, and the reason both live behind a call.
#
# `src/settings.py` owns them, resolving environment > `state/settings.json` >
# default, per call:
#
#     settings.provider()      MNEMO_PROVIDER          -> "local" | "api"
#     settings.api_url()       MNEMO_API_EMBED_URL
#     settings.api_model()     MNEMO_API_EMBED_MODEL
#     settings.api_dim()       MNEMO_API_EMBED_DIM     (mandatory, never guessed:
#                                                       the vec0 column is a fixed
#                                                       width, so a wrong value
#                                                       corrupts the index)
#     settings.api_key()       MNEMO_API_EMBED_KEY
#     settings.api_timeout()   MNEMO_API_EMBED_TIMEOUT
#
# Sending memory to a third party stays a privacy decision: there is NO default
# endpoint, `local` never degrades into `api`, and the only way onto that path
# is naming it — per service (the setting) or per bank (the registry field).


# --- registry & banks (G) -----------------------  service-dev

# The banks registry: one human-editable JSON document listing every memory
# root this machine serves. Hand edits are picked up without a restart (the
# registry re-reads whenever the file's mtime changes), so relocating it is a
# supported move rather than a trick.
#
# Only the OVERRIDE lives here, as a raw string, deliberately. A
# `BANKS_FILE = STATE_DIR / "banks.json"` constant would be evaluated once at
# import and then never follow a later change to STATE_DIR — the frozen-path
# bug that leaked empty databases into the user's real state dir. Anything
# derived from STATE_DIR is computed **when asked**, by the module that owns
# it (`registry.banks_file()`), not cached here.
#
# Same rule for everyone: read `config.STATE_DIR` through the module, never
# `from .config import STATE_DIR`.
BANKS_FILE_OVERRIDE: str | None = os.environ.get("MNEMO_BANKS_FILE")


# --- api / websocket (J) ------------------------  service-dev

# Everything mnemo does goes through this one loopback backend. Port 8918,
# not 8917: the embedding resident already owns 8917.
SERVICE_VERSION: str = "3.0.0"
API_HOST: str = os.environ.get("MNEMO_API_HOST", "127.0.0.1")
API_PORT: int = int(os.environ.get("MNEMO_API_PORT", "8918"))
# The token is 48 hex chars in `STATE_DIR/api.token`, generated on first
# start. The PATH is derived live by `api.token_file()` (see the note in the
# registry section); only the value override belongs here.
API_TOKEN: str | None = os.environ.get("MNEMO_API_TOKEN")
API_URL: str = os.environ.get(
    "MNEMO_API_URL", f"http://{API_HOST}:{API_PORT}"
)
# A face that finds no backend starts one (windowless) and carries on, so the
# service is up on first use rather than after a reboot. 0 disables it — set
# it in CI, where a stray background process outliving the job is worse than
# a failed call.
API_AUTOSTART: bool = os.environ.get("MNEMO_API_AUTOSTART", "1") != "0"
# How long a face waits for a backend it just started before giving up.
API_AUTOSTART_WAIT_S: float = float(
    os.environ.get("MNEMO_API_AUTOSTART_WAIT_S", "20")
)
# WebSocket: keepalive, and the ceiling on index_progress chatter (§9.7).
WS_PING_INTERVAL_S: float = 30.0
WS_PROGRESS_THROTTLE_MS: int = 200


# --- queue & watcher (E, F) ---------------------  service-dev

# 0 -> pure FIFO: every task becomes NORMAL and nothing is preempted. On by
# default so a single edit never waits behind a full rebuild.
QUEUE_PRIORITY: bool = os.environ.get("MNEMO_QUEUE_PRIORITY", "1") != "0"
# Priority alone is one-sided: a sustained stream of HIGH (an active editing
# session is exactly that) would keep a bulk build from ever finishing. After
# N consecutive HIGH tasks the worker takes one LOW regardless, so both
# directions are bounded. 0 disables aging.
QUEUE_AGING: int = int(os.environ.get("MNEMO_QUEUE_AGING", "8"))
# One worker: the backend is the only writer to a bank's index.
WORKERS: int = int(os.environ.get("MNEMO_WORKERS", "1"))
# Collapse a storm of saves — an editor writing, a formatter rewriting right
# after, a `git checkout` touching hundreds of files — into one reindex.
DEBOUNCE_MS: int = int(os.environ.get("MNEMO_DEBOUNCE_MS", "800"))
# Catch up on whatever changed while the service was down.
RECONCILE_ON_START: bool = os.environ.get("MNEMO_RECONCILE_ON_START", "1") != "0"
# Safety net: watchdog can miss events (network shares, a suspended laptop, a
# burst that overflows the OS buffer), and a missed delete is invisible
# forever otherwise. A rescan is a bulk — scan and hash-diff, no embedding —
# so an idle bank costs one directory walk. 0 disables it.
RESCAN_INTERVAL_S: float = float(
    os.environ.get("MNEMO_RESCAN_INTERVAL_S", "900")
)


# --- service log & retention (I) ----------------  service-dev

# Query and index events live in `STATE_DIR/service.db`, written only by the
# backend, one connection, WAL. The path is derived live by
# `servicelog.db_path()` — see the frozen-path note in the registry section.
LOG_RETENTION_DAYS: int = int(os.environ.get("MNEMO_LOG_RETENTION_DAYS", "30"))
# Row backstop for a machine that queries far more than it sleeps; 0 = off.
LOG_MAX_ROWS: int = int(os.environ.get("MNEMO_LOG_MAX_ROWS", "200000"))
# Pruned at start and on this interval.
LOG_PRUNE_INTERVAL_S: float = 6 * 3600


# --- process lifecycle (L) ----------------------  platform-dev

# Process-state file (contracts §11.2). Owned by the backend: it publishes
# pid/host/port/version here on startup and removes the file on a clean exit.
SERVICE_INFO_FILE: Path = STATE_DIR / "service.json"

# Spawn-identity file, owned by service_ctl. Deliberately NOT service.json:
# the backend rewrites that file wholesale, which would erase the one field
# that makes liveness trustworthy. A bare PID is not proof the process is
# ours (the OS reuses PIDs), so this records the process creation time as
# well — see service_ctl.process_fingerprint.
#
# Like SERVICE_INFO_FILE above, this is ``STATE_DIR / …`` evaluated at import
# and therefore frozen: relocating ``config.STATE_DIR`` afterwards does not
# move it. Both are kept only as the documented default location — the live
# accessors ``service_ctl.service_pid_file()`` / ``service_info_file()`` and
# ``api.service_info_file()`` are what the code must use.
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

# How long `service start` then waits for /health to answer, so that
# "started" means "serving" rather than "spawned". A cold first start pays
# for importing FastAPI, both MCP faces and the store before it binds, which
# on a clean machine measured well past the grace above -- long enough that
# the installer's closing `doctor` reported the backend DOWN on a perfectly
# good install. Set to 0 to return as soon as the process exists.
SERVICE_READY_TIMEOUT: float = float(
    os.environ.get("MNEMO_SERVICE_READY_TIMEOUT", "45.0")
)
