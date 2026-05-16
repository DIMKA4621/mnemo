"""Central configuration.

Single place to change the embedding model, paths and search knobs so the
rest of the code never hard-codes them.
"""
from __future__ import annotations

from pathlib import Path

# Repo root = parent of the `src/` package directory.
ROOT_DIR: Path = Path(__file__).resolve().parent.parent

# Curated markdown lives under .claude/, mirroring a real project layout.
CLAUDE_DIR: Path = ROOT_DIR / ".claude"
PROJECT_MEMORY_DIR: Path = CLAUDE_DIR / "memory"
AGENT_MEMORY_DIR: Path = CLAUDE_DIR / "agent-memory"

# Disposable, gitignored vector index (rebuildable from the .md at any time).
DB_PATH: Path = ROOT_DIR / "memory.db"

# Embedding model. Decision (2026-05-16) was multilingual-e5-base, but
# fastembed 0.8.0 ships no e5-base — only e5-large among strong multilingual
# models. We use e5-large: same e5 family (same query:/passage: convention),
# best multilingual quality available; heavier (~2.24 GB) but accepted.
# Same family, so an eventual e5-base build is a one-line swap.
EMBEDDING_MODEL: str = "intfloat/multilingual-e5-large"
EMBEDDING_DIM: int = 1024

# Chunking: heading-aware; a small file naturally becomes one whole chunk.
CHUNK_CAPACITY: tuple[int, int] = (200, 1200)  # (min, max) characters

# Search knobs.
TOP_K: int = 5
RRF_K: int = 60  # reciprocal-rank-fusion constant
