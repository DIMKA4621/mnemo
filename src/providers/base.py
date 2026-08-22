"""The embedding seam: one interface, ``texts[] -> vecs[]``.

Everything above this line (indexer, search) must not know how a vector is
produced. That is what makes the local ONNX resident and a remote API
endpoint interchangeable, and what lets a bank record *which* provider built
its vectors so two models never end up mixed in one database.

Two methods, not one, because the e5 family is asymmetric: documents are
embedded as ``passage: ...`` and queries as ``query: ...``. A provider that
does not care can implement both the same way.

Contract (Memory-contracts-v3 §2): ``embed_passages`` returns exactly
``len(texts)`` vectors of width ``dim``, in order; an empty input returns an
empty list without touching the model; a provider NEVER downloads a model —
explicit ``warmup`` remains the only thing that does; a provider does not log
and knows nothing about banks or the queue.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


# Ceiling on `longest x count` for one embed call, in characters — the
# conservative value, used by any provider that has not measured its own.
# See `EmbeddingProvider.pad_budget` for why the safe direction is *wide*.
DEFAULT_PAD_BUDGET = 19200


class EmbeddingUnavailable(RuntimeError):
    """Provider cannot produce vectors right now (daemon down, API error,
    model not cached). Callers degrade; they never crash.

    Raised rather than returning nothing, so the choice is explicit: the
    indexer aborts the file (it must never write a chunk without its vector),
    search returns no results. Never means "bad input".
    """


class EmbeddingProvider(ABC):
    """Turns text into vectors. Implementations: ``local``, later ``api``."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identity, e.g. "local" | "api"."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier, e.g. "intfloat/multilingual-e5-large"."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Vector dimensionality — the width of the sqlite-vec column."""

    @property
    def key(self) -> str:
        """Rebuild fingerprint stored in the bank DB.

        Any change here means the stored vectors are no longer comparable to
        newly produced ones, so the bank is rebuilt from the .md (FR-8).
        """
        return f"{self.name}:{self.model}:{self.dim}"

    @property
    def pad_budget(self) -> int:
        """Ceiling on ``longest x count`` for one embed call, in characters.

        A batch is padded to its longest member, so this bounds what a call
        actually costs rather than how many items it carries. It lives on the
        provider because **the two backends want opposite things**, measured
        on one corpus with one model (``multilingual-e5-large``), only the
        backend differing:

        | budget | calls | CPU resident | Ollama on a GPU |
        |--------|-------|--------------|-----------------|
        | 19200  |     9 | 1.00x        | **1.00x**       |
        | 2400   |    26 | 1.27x        | 0.71x           |
        | 1200   |    49 | **1.38x**    | 0.50x           |

        A CPU pays for every padding token and so wants narrow batches; a GPU
        pads for free but pays ~0.34s per call and so wants wide ones. One
        shared constant therefore cannot be right for both: the CPU's best
        value makes the GPU **twice as slow**, with no error to notice it by.

        The default is deliberately the conservative end. An unknown endpoint
        behind ``api`` is far likelier to resemble the GPU (network round
        trips dominate) than the resident, and being wrong here is silent.
        ``local`` overrides it downward, where it was measured.
        """
        return DEFAULT_PAD_BUDGET

    @abstractmethod
    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        """Embed documents for indexing. Raises EmbeddingUnavailable."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed one search query. Raises EmbeddingUnavailable."""

    def health(self) -> bool:
        """Cheap liveness probe; never raises. The default assumes healthy."""
        return True
