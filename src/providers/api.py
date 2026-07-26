"""`api` provider — embeddings from an external, OpenAI-shaped endpoint.

Opt-in, and only ever reached by being named: `MNEMO_PROVIDER=api` for the
service or a bank's `provider` field in the registry. Nothing falls back to
it. That is a privacy property, not a preference — this is the one path that
sends the contents of a memory bank off the machine, so it must be
impossible to arrive here by accident or by a degradation.

Wire format (Memory-contracts-v3 §2.2):

    POST $MNEMO_API_EMBED_URL
    {"model": "...", "input": ["text", ...]}
    -> {"data": [{"embedding": [...]}, ...]}

No ``passage:`` / ``query:`` prefixes: those are an e5 convention and belong
to the local provider. An endpoint that wants them can be given a model that
applies them server-side.
"""
from __future__ import annotations

from ..config import (
    API_EMBED_DIM,
    API_EMBED_KEY,
    API_EMBED_MODEL,
    API_EMBED_TIMEOUT,
    API_EMBED_URL,
)
from .base import EmbeddingProvider, EmbeddingUnavailable


class ApiProvider(EmbeddingProvider):
    """An external embeddings service, addressed over HTTP."""

    def __init__(self) -> None:
        missing = [
            name
            for name, value in (
                ("MNEMO_API_EMBED_URL", API_EMBED_URL),
                ("MNEMO_API_EMBED_MODEL", API_EMBED_MODEL),
                ("MNEMO_API_EMBED_DIM", API_EMBED_DIM),
            )
            if not value
        ]
        if missing:
            # Refuse at construction, not at the first embed: a provider that
            # only fails once a bulk index is underway has already wasted the
            # user's time and half-written an index.
            raise ValueError(
                f"the `api` provider needs {', '.join(missing)}. "
                f"MNEMO_API_EMBED_DIM has no default on purpose — the vector "
                f"column is a fixed width and guessing it corrupts the index."
            )

    @property
    def name(self) -> str:
        return "api"

    @property
    def model(self) -> str:
        return API_EMBED_MODEL

    @property
    def dim(self) -> int:
        return API_EMBED_DIM

    def _post(self, inputs: list[str]) -> list[list[float]]:
        import httpx

        headers = {"Content-Type": "application/json"}
        if API_EMBED_KEY:
            headers["Authorization"] = f"Bearer {API_EMBED_KEY}"
        try:
            response = httpx.post(
                API_EMBED_URL,
                json={"model": API_EMBED_MODEL, "input": inputs},
                headers=headers,
                timeout=API_EMBED_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # The body usually says what is actually wrong (bad key, unknown
            # model, rate limit); a bare status code sends people guessing.
            detail = exc.response.text[:200].replace("\n", " ")
            raise EmbeddingUnavailable(
                f"{API_EMBED_URL} returned {exc.response.status_code}: {detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingUnavailable(
                f"cannot reach the embedding endpoint {API_EMBED_URL}: {exc}"
            ) from exc

        try:
            vectors = [item["embedding"] for item in payload["data"]]
        except (KeyError, TypeError) as exc:
            raise EmbeddingUnavailable(
                f"{API_EMBED_URL} answered in an unexpected shape; expected "
                f'{{"data": [{{"embedding": [...]}}]}}, got {str(payload)[:160]}'
            ) from exc

        if len(vectors) != len(inputs):
            # Silently short output would misalign chunks and their vectors,
            # which is unrecoverable once written.
            raise EmbeddingUnavailable(
                f"asked for {len(inputs)} embeddings, got {len(vectors)}"
            )
        for vector in vectors:
            if len(vector) != API_EMBED_DIM:
                raise EmbeddingUnavailable(
                    f"endpoint returned {len(vector)}-dim vectors but "
                    f"MNEMO_API_EMBED_DIM says {API_EMBED_DIM}; the index "
                    f"column cannot hold these"
                )
        return vectors

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._post(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._post([text])[0]

    def health(self) -> bool:
        """Configured is as far as we check — a probe would be a paid call."""
        return bool(API_EMBED_URL and API_EMBED_MODEL and API_EMBED_DIM)
