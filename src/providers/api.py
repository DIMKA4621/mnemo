"""`api` provider — embeddings from an external, OpenAI-shaped endpoint.

Opt-in, and only ever reached by being named: `MNEMO_PROVIDER=api` for the
service or a bank's `provider` field in the registry. Nothing falls back to
it. That is a privacy property, not a preference — this is the one path that
sends the contents of a memory bank off the machine, so it must be
impossible to arrive here by accident or by a degradation.

Wire format (Memory-contracts-v3 §2.2):

    POST <api.url>
    {"model": "...", "input": ["text", ...]}
    -> {"data": [{"embedding": [...]}, ...]}

Configuration is read through ``settings`` **per call**, never imported as a
constant: these are the values the console edits, and an import-time binding
would serve the value the process started with forever (the same frozen-path
scar as ``BANKS_FILE``). Each is resolved environment > ``settings.json`` >
default.

**Prefixes are applied here when the model needs them**, which reverses this
module's original rule ("no prefixes — that is an e5 detail belonging to
`local`"). That rule held only while `api` meant "somebody else's endpoint".
It is a URL, so it can address the very model mnemo ships:
``zylonai/multilingual-e5-large`` in Ollama is e5, and e5 is trained with
mandatory ``passage: `` / ``query: `` markers. Sending it bare text produced
the same vectors as a different, worse system, with nothing in a log to say
so.

They are not a setting a person types, because whoever forgot the field would
hit exactly the same silent failure. They belong to the **model**, so
``presets`` records them next to it and a name is enough to get them right.
An unlisted model gets none, which is correct for most embeddings; an
explicit setting can still override either way.
"""
from __future__ import annotations

from .. import presets, settings
from .base import EmbeddingProvider, EmbeddingUnavailable


class ApiProvider(EmbeddingProvider):
    """An external embeddings service, addressed over HTTP."""

    def __init__(self) -> None:
        # Read once here and held for this instance's life: `dim` and `model`
        # go into the bank's `provider_key`, so they must not change under a
        # running index. A settings edit takes effect on the next service
        # start, which is exactly what the console promises.
        self._url = settings.api_url()
        self._model = settings.api_model()
        self._dim = settings.api_dim()
        # From the catalogue by default; an explicit setting wins, so a model
        # we have not catalogued is still usable with the right markers.
        catalogue = presets.prefixes(self._model)
        self._passage_prefix = settings.api_passage_prefix(catalogue[0])
        self._query_prefix = settings.api_query_prefix(catalogue[1])
        missing = [
            name
            for name, value in (
                ("url", self._url),
                ("model", self._model),
                ("dim", self._dim),
            )
            if not value
        ]
        if missing:
            # Refuse at construction, not at the first embed: a provider that
            # only fails once a bulk index is underway has already wasted the
            # user's time and half-written an index.
            raise ValueError(
                f"the `api` provider needs {', '.join(missing)} — set them in "
                f"{settings.settings_file()} under \"api\", or as "
                f"MNEMO_API_EMBED_{'/'.join(m.upper() for m in missing)}. "
                f"`dim` has no default on purpose: the vector column is a "
                f"fixed width and guessing it corrupts the index."
            )

    @property
    def name(self) -> str:
        return "api"

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def key(self) -> str:
        """Rebuild fingerprint — and the prefixes are part of it.

        Without them this would be the same silent-corruption hazard as an
        unrecorded chunker rule: turning ``passage: `` on or off changes every
        vector this endpoint produces, while ``name:model:dim`` stays
        identical. `reconcile` only re-embeds files whose sha256 moved, so one
        database would end up holding vectors from two different embeddings of
        the same model, with nothing to detect it.

        The prefixes are hashed rather than spelled out: they can be arbitrary
        text, and a key is compared, never parsed.
        """
        base = super().key
        if not (self._passage_prefix or self._query_prefix):
            return base
        import hashlib

        digest = hashlib.sha1(
            f"{self._passage_prefix}\x00{self._query_prefix}".encode("utf-8")
        ).hexdigest()[:8]
        return f"{base}:p{digest}"

    def _post(self, inputs: list[str]) -> list[list[float]]:
        import httpx

        headers = {"Content-Type": "application/json"}
        # The key IS read per call: rotating a credential must not need a
        # restart, and unlike `dim`/`model` it does not describe the vectors.
        key = settings.api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            response = httpx.post(
                self._url,
                json={"model": self._model, "input": inputs},
                headers=headers,
                timeout=settings.api_timeout(),
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            # The body usually says what is actually wrong (bad key, unknown
            # model, rate limit); a bare status code sends people guessing.
            detail = exc.response.text[:200].replace("\n", " ")
            raise EmbeddingUnavailable(
                f"{self._url} returned {exc.response.status_code}: {detail}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingUnavailable(
                f"cannot reach the embedding endpoint {self._url}: {exc}"
            ) from exc

        try:
            vectors = [item["embedding"] for item in payload["data"]]
        except (KeyError, TypeError) as exc:
            raise EmbeddingUnavailable(
                f"{self._url} answered in an unexpected shape; expected "
                f'{{"data": [{{"embedding": [...]}}]}}, got {str(payload)[:160]}'
            ) from exc

        if len(vectors) != len(inputs):
            # Silently short output would misalign chunks and their vectors,
            # which is unrecoverable once written.
            raise EmbeddingUnavailable(
                f"asked for {len(inputs)} embeddings, got {len(vectors)}"
            )
        for vector in vectors:
            if len(vector) != self._dim:
                raise EmbeddingUnavailable(
                    f"endpoint returned {len(vector)}-dim vectors but the "
                    f"configured dim is {self._dim}; the index column cannot "
                    f"hold these"
                )
        return vectors

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._post([f"{self._passage_prefix}{t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._post([f"{self._query_prefix}{text}"])[0]

    def health(self) -> bool:
        """Configured is as far as we check — a probe would be a paid call."""
        return bool(self._url and self._model and self._dim)
