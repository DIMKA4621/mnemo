"""Central configuration — project-agnostic.

The engine is installed once at user scope and serves ANY project: the
project root is passed in (CLI --root, default cwd). Per-project index
DBs and the shared model cache live under the user-scope home.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

# User-scope home: installed once, shared by all projects.
USER_HOME: Path = Path(
    os.environ.get("MNEMO_HOME", Path.home() / ".claude" / "mnemo")
)
STATE_DIR: Path = USER_HOME / "state"        # one <projhash>.db per project
MODEL_CACHE: Path = USER_HOME / "model-cache"  # e5-large, once for all projects

# Embedding model. Decision was multilingual-e5-base; fastembed 0.8.0 ships
# no e5-base, so e5-large (same family, 1024-dim) is the documented fallback.
EMBEDDING_MODEL: str = "intfloat/multilingual-e5-large"
EMBEDDING_DIM: int = 1024

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

# Inject telemetry log — JSONL, user-scope, rotated, ONE FILE PER PROJECT.
# Path: ``state/logs/<projhash>.log`` — same <projhash> as the project's
# index DB so a log line and its index sit side by side. One line per
# hook-inject call (ok / skipped / errored) so MIN_SIM / TOP_N / gate
# behaviour is tunable from real data instead of guesswork. Lives outside
# git on purpose: project-agnostic, machine-local. Orphan logs from
# deleted projects can be pruned manually (files are tiny).
INJECT_LOG_DIR: Path = USER_HOME / "state" / "logs"
INJECT_LOG_MAX_BYTES: int = 5 * 1024 * 1024   # 5 MB per file
INJECT_LOG_BACKUPS: int = 3                   # 3 rotated → ~20 MB cap per project
INJECT_LOG_PROMPT_CHARS: int = 200            # truncate prompt in the log
# Weak-match gate (auto-inject path only; manual search is never gated).
# Cosine-similarity floor on the vector leg + a minimum query length.
# e5 has a high baseline similarity (anisotropy); measured on the test
# corpus: relevant top hits ~0.84-0.87, junk/off-topic ~0.78-0.81. 0.83
# keeps every relevant top-1 and cuts all junk — but the margin is narrow
# (~0.03), so this is PROVISIONAL: recalibrate on real prompts at the pilot.
MIN_SIM: float = 0.83
MIN_QUERY_CHARS: int = 8

# Warm embedding helper (resident model holder). Loopback TCP — NOT a unix
# socket: CPython does not expose socket.AF_UNIX on Windows, and we need
# Linux/macOS/Windows parity with zero OS-specific quirks.
EMBED_HOST: str = "127.0.0.1"
EMBED_PORT: int = int(os.environ.get("MNEMO_EMBED_PORT", "8917"))
EMBED_TOKEN_FILE: Path = USER_HOME / "state" / "embed.token"
EMBED_IDLE_TIMEOUT: int = 1800  # resident exits after 30 min idle

# Embedding CPU cap. ONNX Runtime defaults to ALL cores per embed call;
# the serial resident under multi-agent load then pegs the whole machine.
# Bound every embed (resident, in-process fallback, ingest, tests, warmup)
# to a fraction of the *available* CPUs. sched_getaffinity honours
# cgroup/taskset limits on Linux; cpu_count is the cross-platform fallback.
# MNEMO_EMBED_THREADS overrides the computed value explicitly.
EMBED_THREADS_DIVISOR: int = 3


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
    return max(1, cpu // EMBED_THREADS_DIVISOR)


EMBED_THREADS: int = _embed_threads()


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved locations for one project root."""

    root: Path
    project_memory: Path   # <root>/.claude/memory
    agent_memory: Path     # <root>/.claude/agent-memory
    db: Path               # user-scope state/<projhash>.db


def resolve(root: Path | str | None) -> ProjectPaths:
    """Resolve all paths for a project root.

    Precedence: explicit arg > $MNEMO_ROOT > current directory.
    The env override lets an MCP server (spawned with an arbitrary cwd)
    be pinned to the right project.
    """
    chosen = root or os.environ.get("MNEMO_ROOT")
    root_path = Path(chosen).resolve() if chosen else Path.cwd().resolve()
    claude = root_path / ".claude"
    proj_hash = hashlib.sha1(str(root_path).encode()).hexdigest()[:16]
    return ProjectPaths(
        root=root_path,
        project_memory=claude / "memory",
        agent_memory=claude / "agent-memory",
        db=STATE_DIR / f"{proj_hash}.db",
    )
