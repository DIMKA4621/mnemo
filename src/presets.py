"""Known embedding backends and models — the catalogue the settings form fills from.

This exists because of a bug that cannot be fixed by asking the user more
carefully. The e5 family is trained with mandatory ``passage: `` / ``query: ``
prefixes; ``local`` applies them (``embedder.py``), and the ``api`` provider
originally did not, on the reasoning that prefixes are "an e5 detail that
belongs to local". But ``api`` is only a URL, and it can be pointed **at e5** —
``zylonai/multilingual-e5-large`` in Ollama is the very model this project
ships. Same model, same corpus, silently worse retrieval, and nothing in a log
to hint at why.

Making the prefix a hand-typed setting would only move the failure: whoever
forgets the field gets the same silent degradation. So the prefix is not
configuration a person supplies — it is a **property of the model**, recorded
here beside it. Pick the model, get its prefixes.

The catalogue is a convenience, never a gate: an unlisted model stays fully
usable and simply defaults to no prefixes, which is what most non-e5
embeddings want. ``KNOWN_MODELS`` is matched loosely, because the same weights
travel under many names (``bge-m3``, ``bge-m3:latest``,
``BAAI/bge-m3``).

Deliberately NOT here:

* **dim as a promise.** Values below are what these models publish, so the
  form can pre-fill them — but the ``api`` provider still verifies the width
  of the first vector it receives. A catalogue entry is a hint; the endpoint
  is the authority, and a wrong width corrupts an index.
* **API keys.** Presets carry no credentials, only shapes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelPreset:
    """One embedding model, and what a client must know to use it right."""

    name: str                 # what the endpoint is asked for
    label: str                # what the form shows
    dim: int
    # The prefixes the model was trained with. Empty for the majority; e5 is
    # the notable exception, and getting this wrong is invisible.
    passage_prefix: str = ""
    query_prefix: str = ""
    note: str = ""

    @property
    def prefixed(self) -> bool:
        return bool(self.passage_prefix or self.query_prefix)


@dataclass(frozen=True)
class BackendPreset:
    """One place embeddings can come from."""

    id: str                   # 'local' | 'ollama' | 'openai'
    label: str
    provider: str             # the provider that implements it: local | api
    url: str = ""
    models: list[ModelPreset] = field(default_factory=list)
    needs_key: bool = False
    note: str = ""


# The e5 contract, in one place so a second model in the family cannot be
# added with the prefixes spelled differently.
_E5_PASSAGE = "passage: "
_E5_QUERY = "query: "


LOCAL_MODEL = ModelPreset(
    name="intfloat/multilingual-e5-large",
    label="multilingual-e5-large",
    dim=1024,
    passage_prefix=_E5_PASSAGE,
    query_prefix=_E5_QUERY,
    note="ships with mnemo; ~2.2 GB, runs on the CPU resident",
)

BACKENDS: list[BackendPreset] = [
    BackendPreset(
        id="local",
        label="Локальний резидент",
        provider="local",
        models=[LOCAL_MODEL],
        note="нічого не треба налаштовувати; працює без мережі",
    ),
    BackendPreset(
        id="ollama",
        label="Ollama",
        provider="api",
        url="http://127.0.0.1:11434/v1/embeddings",
        models=[
            ModelPreset(
                name="bge-m3",
                label="bge-m3",
                dim=1024,
                note="рекомендована: без префіксів, вікно 8K, швидка на GPU",
            ),
            ModelPreset(
                name="zylonai/multilingual-e5-large",
                label="multilingual-e5-large",
                dim=1024,
                passage_prefix=_E5_PASSAGE,
                query_prefix=_E5_QUERY,
                note="та сама модель, що в локального резидента",
            ),
            ModelPreset(
                name="nomic-embed-text",
                label="nomic-embed-text",
                dim=768,
                passage_prefix="search_document: ",
                query_prefix="search_query: ",
                note="теж з префіксами, але своїми — не e5-івськими",
            ),
        ],
        note="локальна служба; з відеокартою ~8.8x на перебудові банку",
    ),
    BackendPreset(
        id="openai",
        label="OpenAI",
        provider="api",
        url="https://api.openai.com/v1/embeddings",
        needs_key=True,
        models=[
            ModelPreset(name="text-embedding-3-small",
                        label="text-embedding-3-small", dim=1536),
            ModelPreset(name="text-embedding-3-large",
                        label="text-embedding-3-large", dim=3072),
        ],
        note="памʼять банку йде за межі машини — окреме рішення, не дефолт",
    ),
]


# Every model any backend knows, for prefix lookup by name alone. Later
# entries do not overwrite earlier ones: `local` is listed first, so its
# spelling of e5 wins over Ollama's mirror of the same weights.
#
# Indexed under the bare last segment as well, because that is a name people
# genuinely use — ``multilingual-e5-large`` is a valid Ollama tag, and a
# lookup that only knew ``intfloat/multilingual-e5-large`` would drop the
# markers for exactly the person who typed the short form.
KNOWN_MODELS: dict[str, ModelPreset] = {}
for _backend in BACKENDS:
    for _model in _backend.models:
        _full = _model.name.lower()
        KNOWN_MODELS.setdefault(_full, _model)
        KNOWN_MODELS.setdefault(_full.rsplit("/", 1)[-1], _model)


def _normalise(name: str) -> str:
    """Strip the decorations the same weights travel under.

    ``bge-m3:latest`` (Ollama's tag), ``BAAI/bge-m3`` (the HF namespace) and
    ``bge-m3`` are one model, and a lookup that missed the difference would
    silently drop the prefixes of whichever spelling was not listed.
    """
    text = name.strip().lower()
    if ":" in text:
        text = text.rsplit(":", 1)[0]
    return text


def find_model(name: str) -> ModelPreset | None:
    """The catalogue entry for a model name, or None if it is not listed.

    Matching is deliberately forgiving: exact, then without a tag, then by
    the last path segment. An unlisted model is not an error — it simply gets
    no prefixes, which is right for most embeddings and wrong only for a
    prefixed family we have not catalogued yet.
    """
    if not name:
        return None
    text = _normalise(name)
    for candidate in (text, text.rsplit("/", 1)[-1]):
        found = KNOWN_MODELS.get(candidate)
        if found is not None:
            return found
    return None


def prefixes(name: str) -> tuple[str, str]:
    """``(passage_prefix, query_prefix)`` for a model name; empty when unknown."""
    found = find_model(name)
    if found is None:
        return ("", "")
    return (found.passage_prefix, found.query_prefix)


def as_json() -> list[dict]:
    """The catalogue as the settings form consumes it."""
    return [
        {
            "id": backend.id,
            "label": backend.label,
            "provider": backend.provider,
            "url": backend.url,
            "needs_key": backend.needs_key,
            "note": backend.note,
            "models": [
                {
                    "name": model.name,
                    "label": model.label,
                    "dim": model.dim,
                    "prefixed": model.prefixed,
                    "note": model.note,
                }
                for model in backend.models
            ],
        }
        for backend in BACKENDS
    ]
