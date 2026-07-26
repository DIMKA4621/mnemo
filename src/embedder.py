"""Local embeddings via fastembed (ONNX, no torch).

Model: multilingual-e5-large, cached once at user scope (shared by all
projects). The e5 family needs input prefixes: documents as
``passage: ...``, queries as ``query: ...`` — hidden here.

The 2 GB model download is NOT done implicitly by hooks. `warmup()` is an
explicit, verbose, user-run step; `is_model_cached()` lets callers refuse
to silently download.
"""
from __future__ import annotations

import threading
import warnings
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

# Built once, shared by both request lanes in the resident.
_MODEL: "TextEmbedding | None" = None
_MODEL_LOCK = threading.Lock()


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


def _model() -> TextEmbedding:
    """The one loaded model, built at most once even under concurrency.

    Double-checked locking rather than ``lru_cache``: the resident serves a
    query lane and a batch lane at the same time, and ``lru_cache`` does not
    hold a lock across the miss, so two first-requests racing could each
    build a session and briefly hold two ~1.5 GB copies. The fast path is
    still just a module-global read.
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            _MODEL = _build_model()
    return _MODEL


def _build_model() -> TextEmbedding:
    from fastembed import TextEmbedding

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
            # A short-lived CLI process never notices, but the resident holds
            # one session for its whole life, and v3 gives it no idle exit —
            # so an inflated arena now stays inflated until the user runs
            # `mnemo service stop`. Disabling it makes allocations transient,
            # freed after each run, so RSS tracks live tensors.
            #
            # Peak RSS depends on BATCH SIZE and the LONGEST SEQUENCE in a
            # batch, not on the thread ceiling. Measured at BATCH_SIZE=16:
            # ~1.56 GB on short English chunks, ~2.07 GB on Ukrainian
            # markdown up to 1200 chars (512-token cap). Steady state after
            # a run is ~1.5 GB regardless of workload.
            #
            # ALWAYS RECORD THE WORKLOAD WITH THE NUMBER. The figure this
            # replaced ("1563 MB") was almost certainly correct — a later
            # sweep measured 1557 MB on short English text, 6 MB away. What
            # went wrong is that nobody wrote down the shape it was taken
            # on, so it hardened from "true under these conditions" into
            # "the number", and looked false the moment the conditions
            # changed. The same applies to the 5407 MB arena-on half of that
            # pair: arena and workload shape are independent axes, so it was
            # a sound comparison at one particular shape, and it has not
            # been reproduced here (it was probably Linux RSS, not Windows
            # working set).
            #
            # Two independent levers, each large, isolated one at a time:
            #   fixed sequence length, batch  4 ->  16 : +311 MB
            #   fixed batch, longest 54 -> 447 tokens  : +376 MB
            # The thread ceiling is not a lever: 9 vs 4 threads differ by
            # 1.0 MB (0.05%) across six runs, on quiet and busy machines
            # alike.
            #
            # A batch is NOT simply padded to its longest member — measured,
            # against the obvious assumption. Two batch-16 arms whose longest
            # member truncates to the same 512 tokens: all-long cost 736 MB
            # transient, mixed-with-a-long-tail cost 539 MB. Full padding
            # would have made them identical. fastembed is already
            # length-aware, so short members really are cheaper, and sorting
            # chunks by length before batching would reclaim far less than
            # it appears — which is why we did not build it.
            #
            # Cost is ~25% per big ingest batch; the query path embeds one
            # text, so search latency is unaffected.
            #
            # NFR-9's "~1.6 GB" describes the STEADY state (1504-1526 MB
            # across every condition measured), not the peak. The resident
            # no longer idle-exits, so that steady figure is what the
            # machine carries until `mnemo service stop`.
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
