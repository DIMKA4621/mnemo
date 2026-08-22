"""Embedding providers — the one place that knows how vectors are made.

``get_provider()`` is the only entry point the rest of the engine uses, so
adding a provider (`api`, phase 7) is one new module plus one line here.
"""
from __future__ import annotations

from functools import lru_cache

from .. import settings
from .base import EmbeddingProvider, EmbeddingUnavailable

__all__ = [
    "EmbeddingProvider",
    "EmbeddingUnavailable",
    "forget_providers",
    "get_provider",
]


@lru_cache(maxsize=None)
def _build(chosen: str) -> EmbeddingProvider:
    """Construct one provider. Cached on the RESOLVED name, not on the
    argument: ``get_provider(None)`` must not pin whatever the machine
    default happened to be the first time it was called."""
    if chosen == "local":
        from .local import LocalProvider

        return LocalProvider()
    if chosen == "api":
        # Only ever reached by being named. Nothing degrades into `api`:
        # it is the one path that sends bank contents off the machine.
        from .api import ApiProvider

        return ApiProvider()
    raise ValueError(f"unknown embedding provider {chosen!r} (known: local, api)")


def get_provider(spec: str | None = None) -> EmbeddingProvider:
    """Resolve a provider by name.

    Precedence (Memory-contracts-v3 §2.3): explicit ``spec`` → the bank's
    ``provider`` field (the caller passes it as ``spec``) → the machine
    setting (``MNEMO_PROVIDER`` or ``settings.json``) → ``"local"``.

    The machine default is read **here, per call**, and only the resolved name
    is memoised. Reading it at import — as this did — meant the console could
    store a new provider, the service could restart its settings, and every
    caller would still be handed the provider the module was imported with.
    """
    chosen = (spec or settings.provider()).strip().lower()
    return _build(chosen)


def forget_providers() -> None:
    """Drop the construction cache — after a settings change, and in tests.

    Providers are handles, but ``api`` snapshots url/model/dim at
    construction (they define ``provider_key``), so a cached instance
    outlives an edit that was meant to replace it.
    """
    _build.cache_clear()
