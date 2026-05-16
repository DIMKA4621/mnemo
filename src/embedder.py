"""Local embeddings via fastembed (ONNX, no torch).

Model: multilingual-e5-base. The e5 family requires input prefixes:
documents are embedded as ``passage: ...``, queries as ``query: ...``.
That convention is hidden here so callers never deal with it.
"""
from __future__ import annotations

import warnings
from functools import lru_cache

from fastembed import TextEmbedding

# fastembed >=0.6 uses mean pooling for e5 (the canonical e5 behaviour);
# its compatibility warning is noise for us — silence just that one.
warnings.filterwarnings("ignore", message=".*mean pooling instead of CLS.*")

from .config import EMBEDDING_MODEL


def _assert_model_available(model: str) -> None:
    """Fail early with a helpful message if fastembed lacks the model."""
    supported = {m["model"] for m in TextEmbedding.list_supported_models()}
    if model not in supported:
        multilingual = sorted(s for s in supported if "multilingual" in s.lower())
        raise SystemExit(
            f"Embedding model {model!r} is not in this fastembed build.\n"
            "Multilingual models available:\n  "
            + "\n  ".join(multilingual or sorted(supported))
            + "\nAdjust EMBEDDING_MODEL / EMBEDDING_DIM in src/config.py."
        )


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    _assert_model_available(EMBEDDING_MODEL)
    return TextEmbedding(model_name=EMBEDDING_MODEL)


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Embed documents for indexing."""
    prefixed = [f"passage: {t}" for t in texts]
    return [vec.tolist() for vec in _model().embed(prefixed)]


def embed_query(text: str) -> list[float]:
    """Embed a single search query."""
    return next(_model().embed([f"query: {text}"])).tolist()
