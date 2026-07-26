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
from typing import TYPE_CHECKING

from .config import EMBED_THREADS, EMBEDDING_MODEL, MODEL_CACHE

if TYPE_CHECKING:  # `fastembed` is imported lazily — see below
    from fastembed import TextEmbedding

# fastembed costs ~1 s to import (9 s on a cold filesystem): it drags in
# onnxruntime, tokenizers and its own model registry. Importing it at module
# level made every `mnemo` invocation pay that, including the ones that never
# embed anything — a no-op `ingest`, a `hook-postedit` on a .py file. Those
# run per keystroke-ish in a live session, so the import happens where it is
# actually needed instead.

# fastembed >=0.6 uses mean pooling for e5 (the canonical e5 behaviour);
# its compatibility warning is noise for us — silence just that one.
warnings.filterwarnings("ignore", message=".*mean pooling instead of CLS.*")


def _model_cache_spec() -> tuple[str, set[str]] | None:
    """Return the FastEmbed repository and required files for our model."""
    from fastembed import TextEmbedding

    for metadata in TextEmbedding.list_supported_models():
        if metadata.get("model") != EMBEDDING_MODEL:
            continue
        sources = metadata.get("sources") or {}
        repository = sources.get("hf")
        model_file = metadata.get("model_file")
        if not repository or not model_file:
            return None
        required = {
            "config.json",
            "tokenizer.json",
            "tokenizer_config.json",
            str(model_file),
        }
        required.update(str(name) for name in metadata.get("additional_files", []))
        return str(repository), required
    return None


def is_model_cached() -> bool:
    """True only when our configured model has a complete local snapshot.

    The cheap negative comes first: an absent or empty cache directory cannot
    hold the model, and answering that needs no fastembed import. Callers ask
    this on the degraded path — a search on a machine that was never warmed
    up — where paying a one-second import to learn "no" is the wrong trade.
    """
    if not MODEL_CACHE.is_dir() or not any(MODEL_CACHE.iterdir()):
        return False
    spec = _model_cache_spec()
    if spec is None:
        return False
    repository, required = spec
    expected_root = f"models--{repository.replace('/', '--')}".lower()
    model_file = next(name for name in required if name.endswith(".onnx"))
    for model_root in MODEL_CACHE.iterdir():
        if not model_root.is_dir() or model_root.name.lower() != expected_root:
            continue
        for candidate in model_root.rglob(model_file):
            snapshot = candidate.parent
            if all(
                (snapshot / name).is_file()
                and (snapshot / name).stat().st_size > 0
                for name in required
            ):
                return True
    return False


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
    #
    # ONNX Runtime also busy-spins its intra-op threads between inferences
    # (allow_spinning defaults to "1"). Harmless for the per-message CLI
    # path, but the resident embed-server keeps the session alive, so those
    # EMBED_THREADS workers never sleep and peg whole cores at idle (e.g.
    # 4 threads -> ~350% CPU). fastembed exposes no hook for ORT session
    # config entries, so wrap InferenceSession just while fastembed builds
    # the session and disable spinning; the wrapper is restored immediately.
    import onnxruntime as ort

    _orig_session = ort.InferenceSession

    def _no_spin_session(*args, **kwargs):
        so = kwargs.get("sess_options")
        if so is not None:
            so.add_session_config_entry(
                "session.intra_op.allow_spinning", "0"
            )
            # Bound the CPU memory arena. ONNX Runtime keeps a reusable
            # allocation arena that grows to the largest single run's peak
            # (a full batch of e5-large) and never returns it to the OS.
            # A short-lived CLI process never notices, but the resident
            # holds one session for its whole life: with idle-exit disabled
            # (shared server), one cold ingest inflates it to ~5 GB+ and it
            # stays there. Disabling the arena makes allocations transient —
            # freed after each run — so RSS tracks live tensors (~model
            # footprint) forever. Measured: peak 5407 MB -> 1563 MB, stable
            # across batches; ~25% slower per big ingest batch (the query
            # path embeds one text, so search latency is unaffected).
            so.enable_cpu_mem_arena = False
        return _orig_session(*args, **kwargs)

    ort.InferenceSession = _no_spin_session
    try:
        return TextEmbedding(
            model_name=EMBEDDING_MODEL,
            cache_dir=str(MODEL_CACHE),
            threads=EMBED_THREADS,
        )
    finally:
        ort.InferenceSession = _orig_session


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
