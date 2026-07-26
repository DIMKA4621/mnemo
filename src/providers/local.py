"""`local` provider — the resident ONNX model, with an in-process fallback.

This is the v2 embedding path moved behind the provider interface, with its
two hard rules intact (Memory-contracts-v3 §2.1):

1. ``embed_*_via_server`` first — so no short-lived hook and no long-lived MCP
   process ever loads the ~2.2 GB model itself;
2. resident unreachable → embed in-process ONLY if the model is already
   cached (the v2 safeguard that keeps tests and offline runs working);
3. otherwise ``EmbeddingUnavailable``. An embed must never trigger an
   implicit download: explicit ``warmup`` remains the one thing that fetches
   the model.

Resident autostart is unchanged (``_obtain_socket`` → ``_spawn_server``,
loopback only).
"""
from __future__ import annotations

from ..config import EMBEDDING_DIM, EMBEDDING_MODEL
from .base import EmbeddingProvider, EmbeddingUnavailable

_UNAVAILABLE = (
    "embedding resident unreachable and no local model cached. Start the "
    "resident (MNEMO_EMBED_HOST) or run `mnemo warmup`."
)


class LocalProvider(EmbeddingProvider):
    """multilingual-e5-large via fastembed, held by the embed resident."""

    @property
    def name(self) -> str:
        return "local"

    @property
    def model(self) -> str:
        return EMBEDDING_MODEL

    @property
    def dim(self) -> int:
        return EMBEDDING_DIM

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        from ..embed_server import TokenRejected, embed_passages_via_server

        try:
            vectors = embed_passages_via_server(texts)
        except TokenRejected as exc:
            # A live resident that refuses us is not "unavailable" — falling
            # back would load 2.2 GB beside a working one and run 50x slower
            # in silence. Fail loudly instead; the message names the fix.
            raise EmbeddingUnavailable(str(exc)) from exc
        if vectors is None:
            from ..embedder import embed_passages, is_model_cached

            if not is_model_cached():
                raise EmbeddingUnavailable(_UNAVAILABLE)
            vectors = embed_passages(texts)
        if len(vectors) != len(texts):
            # A short list would silently misalign chunks and vectors.
            raise EmbeddingUnavailable(
                f"provider returned {len(vectors)} vectors for {len(texts)} texts"
            )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        from ..embed_server import TokenRejected, embed_query_via_server

        try:
            vec = embed_query_via_server(text)
        except TokenRejected as exc:
            raise EmbeddingUnavailable(str(exc)) from exc
        if vec is not None:
            return vec

        from ..embedder import embed_query, is_model_cached

        if not is_model_cached():
            raise EmbeddingUnavailable(_UNAVAILABLE)
        return embed_query(text)

    def health(self) -> bool:
        from ..embed_server import server_is_up
        from ..embedder import is_model_cached

        return is_model_cached() or server_is_up()
