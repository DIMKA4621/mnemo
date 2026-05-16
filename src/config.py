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
    os.environ.get("MEMORY_POC_HOME", Path.home() / ".claude" / "memory-poc")
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


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved locations for one project root."""

    root: Path
    project_memory: Path   # <root>/.claude/memory
    agent_memory: Path     # <root>/.claude/agent-memory
    db: Path               # user-scope state/<projhash>.db


def resolve(root: Path | str | None) -> ProjectPaths:
    """Resolve all paths for a project root (default: current directory)."""
    root_path = Path(root).resolve() if root else Path.cwd().resolve()
    claude = root_path / ".claude"
    proj_hash = hashlib.sha1(str(root_path).encode()).hexdigest()[:16]
    return ProjectPaths(
        root=root_path,
        project_memory=claude / "memory",
        agent_memory=claude / "agent-memory",
        db=STATE_DIR / f"{proj_hash}.db",
    )
