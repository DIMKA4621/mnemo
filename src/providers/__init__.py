"""Embedding providers — the one place that knows how vectors are made.

``get_provider()`` is the only entry point the rest of the engine uses, so
adding a provider (`api`, phase 7) is one new module plus one line here.
"""
from __future__ import annotations

from functools import lru_cache

from ..config import EMBED_PROVIDER
from .base import EmbeddingProvider, EmbeddingUnavailable

__all__ = ["EmbeddingProvider", "EmbeddingUnavailable", "get_provider"]


@lru_cache(maxsize=None)
def get_provider(spec: str | None = None) -> EmbeddingProvider:
    """Resolve a provider by name.

    Precedence (Memory-contracts-v3 §2.3): explicit ``spec`` → the bank's
    ``provider`` field (the caller passes it as ``spec``) → ``$MNEMO_PROVIDER``
    → ``"local"``. Cached per spec: providers are stateless handles and the
    local one memoises the loaded model behind it.
    """
    chosen = (spec or EMBED_PROVIDER).strip().lower()
    if chosen == "local":
        from .local import LocalProvider

        return LocalProvider()
    raise ValueError(f"unknown embedding provider {chosen!r} (known: local)")
