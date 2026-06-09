"""Local embeddings via fastembed (ONNX, no torch).

Model: multilingual-e5-large, cached once at user scope (shared by all
projects). The e5 family needs input prefixes: documents as
``passage: ...``, queries as ``query: ...`` — hidden here.

The 2 GB model download is NOT done implicitly by hooks. `warmup()` is an
explicit, verbose, user-run step; `is_model_cached()` lets callers refuse
to silently download.
"""
from __future__ import annotations

import warnings
from functools import lru_cache

from fastembed import TextEmbedding

from .config import EMBED_THREADS, EMBEDDING_MODEL, MODEL_CACHE

# fastembed >=0.6 uses mean pooling for e5 (the canonical e5 behaviour);
# its compatibility warning is noise for us — silence just that one.
warnings.filterwarnings("ignore", message=".*mean pooling instead of CLS.*")


def is_model_cached() -> bool:
    """True if the model is already downloaded under the user-scope cache."""
    if not MODEL_CACHE.exists():
        return False
    needle = EMBEDDING_MODEL.split("/")[-1].lower()
    return any(needle in p.name.lower() for p in MODEL_CACHE.rglob("*"))


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    # Create the cache dir only when missing: on a read-only mount (container
    # with model-cache from the host) the model is already present and an
    # unconditional mkdir on the existing dir can raise EROFS.
    if not MODEL_CACHE.exists():
        MODEL_CACHE.mkdir(parents=True, exist_ok=True)
    # threads caps ONNX intra-op parallelism: without it every embed call
    # fans out across ALL cores and the serial resident pegs the machine
    # under multi-agent load. See config.EMBED_THREADS.
    return TextEmbedding(
        model_name=EMBEDDING_MODEL,
        cache_dir=str(MODEL_CACHE),
        threads=EMBED_THREADS,
    )


def warmup() -> int:
    """Explicitly download + sanity-check the model. Returns vector dim."""
    vec = embed_query("warmup probe — перевірка моделі")
    return len(vec)


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embed documents for indexing."""
    prefixed = [f"passage: {t}" for t in texts]
    return [vec.tolist() for vec in _model().embed(prefixed)]


def embed_query(text: str) -> list[float]:
    """Embed a single search query."""
    return next(_model().embed([f"query: {text}"])).tolist()
