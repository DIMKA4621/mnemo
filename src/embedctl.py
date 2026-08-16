"""Backend memory control: is a model held in RAM, and let go of it.

Separate from ``service_ctl`` (which owns process lifecycle) and from
``providers`` (which owns producing vectors), because this is neither: it
asks a backend how much of the machine it is currently holding, and asks it
to stop. The provider interface deliberately says nothing about memory — an
embeddings endpoint is a URL, and most of them hold nothing on our behalf.

**This is not an off switch.** Design decision, recorded before the code:
a backend that is off is not a mode, it is a fault. Whatever is unloaded
here comes back on the next indexed file or the next search, paying ~7-8 s
once (measured: 7.6 s for the resident, 8.4 s for Ollama). What this buys is
the memory back *now*, on purpose, which is the trade
``MNEMO_EMBED_IDLE_TIMEOUT=0`` deliberately left to a command rather than a
timer (``config.py``: paying 9 s repeatedly to reclaim 1.6 GB that a single
command can reclaim is the wrong trade on a developer machine).

Three backends, three different answers:

* ``local`` — a resident process of ours holding ~1.5 GB. Unloading is
  ``service_ctl.stop_resident()``, which identifies it by the port and by
  our token, never by a PID we think we spawned.
* Ollama — holds the model in VRAM with its own TTL. Unloading is
  ``keep_alive: 0``.
* anything else behind ``api`` (OpenAI and friends) — holds nothing of ours.
  There is nothing here to unload and saying so is the whole answer.

**Ollama is addressed natively, not through ``ApiProvider``, and that is the
finding this module exists for.** ``/v1/embeddings`` is the OpenAI-compatible
shim; it *accepts* ``keep_alive`` and *ignores* it. Measured on this machine:
posting ``keep_alive: 0`` to ``/v1/embeddings`` returned 200 with a correct
1024-wide vector and left the model resident with its expiry merely pushed
out; the same body to the native ``/api/embed`` unloaded it. So routing this
through the provider would have produced a button that reports success and
frees nothing — the worst available outcome, since the user has no way to
see the difference. The cost is that mnemo now knows one backend-specific
fact; it is confined to the ``ollama`` preset id, next to the other things
that are true only of that backend.

Only **our** model is unloaded, never everything Ollama happens to hold:
other models there belong to whoever loaded them, and this cabinet is not
where somebody's chat model gets evicted.
"""
from __future__ import annotations

import time as _time
from typing import Any

# What `state()["holding"]` can say.
HOLD_YES = "loaded"      # the model is in memory right now
HOLD_NO = "unloaded"     # nothing held; the next embed pays the wake-up
HOLD_NONE = "n/a"        # this backend never holds anything for us
HOLD_UNKNOWN = "unknown"  # we could not tell (endpoint unreachable, etc.)


class EmbedControlUnavailable(RuntimeError):
    """The action could not be carried out, with a reason worth showing."""


def _ollama_root(url: str) -> str | None:
    """The Ollama server root for an embeddings URL, or None if not Ollama.

    Matched on the path rather than on the host: an Ollama reachable on a
    LAN address or behind a different port is still Ollama, while a hosted
    OpenAI-compatible endpoint is not, whatever it is called. The catalogue
    entry is the primary signal (`presets`), and this is the fallback for a
    URL somebody typed by hand.
    """
    text = (url or "").strip().rstrip("/")
    for suffix in ("/v1/embeddings", "/api/embed", "/api/embeddings"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return None


def _is_ollama(url: str) -> bool:
    from . import presets

    root = _ollama_root(url)
    if root is None:
        return False
    for backend in presets.BACKENDS:
        if backend.id == "ollama" and backend.url:
            if _ollama_root(backend.url) == root:
                return True
    # A hand-typed Ollama on another host/port still ends in /v1/embeddings,
    # which every OpenAI-compatible endpoint does. Only the native probe can
    # settle it, and `state()` does exactly that before claiming anything.
    return False


def _ollama_ps(root: str, timeout: float = 5.0) -> list[dict] | None:
    """What Ollama is holding, or None when it cannot be asked."""
    import httpx

    try:
        resp = httpx.get(f"{root}/api/ps", timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:  # noqa: BLE001 - unreachable is an answer, not a crash
        return None
    models = payload.get("models")
    return models if isinstance(models, list) else []


def _same_model(held: str, ours: str) -> bool:
    """Does a name Ollama reports denote the model we configured.

    Two decorations have to come off, and forgetting either makes the button
    refuse to unload our own model:

    * the **tag** — Ollama answers ``bge-m3:latest`` for a model configured
      as ``bge-m3``;
    * the **namespace** — the same weights travel as ``BAAI/bge-m3`` and, in
      Ollama's own registry, as ``zylonai/multilingual-e5-large`` for what
      ``local`` calls ``intfloat/multilingual-e5-large``.

    Comparing the bare last segment is what ``presets.find_model`` already
    does for prefix lookup, and for the same reason.
    """
    from . import presets

    def bare(name: str) -> str:
        return presets.normalise(name).rsplit("/", 1)[-1]

    return bare(held) == bare(ours)


def _describe_local() -> dict[str, Any]:
    from . import config
    from .embed_server import server_is_up
    from .embedder import is_model_cached

    up = server_is_up()
    return {
        "backend": "local",
        "model": config.EMBEDDING_MODEL,
        "holding": HOLD_YES if up else HOLD_NO,
        "cached": is_model_cached(),
        "where": f"{config.EMBED_HOST}:{config.EMBED_PORT}",
        # Measured, and worth showing next to the button: this is what the
        # next search pays after unloading, and the number is the whole
        # argument for why the default is a command and not a timer.
        "wake_s": 7.6,
        "detail": None,
    }


def _describe_api() -> dict[str, Any]:
    from . import settings

    url = settings.api_url()
    model = settings.api_model()
    info: dict[str, Any] = {
        "backend": "api",
        "model": model,
        "holding": HOLD_NONE,
        "cached": None,
        "where": url,
        "wake_s": None,
        "detail": None,
    }
    root = _ollama_root(url)
    if root is None:
        # `detail` stays empty: `holding: n/a` already says this, and a client
        # renders a steady state in its own words. `detail` is for the things
        # only the service knows — an address it could not reach, a width that
        # does not match — which no client could phrase for itself.
        return info

    held = _ollama_ps(root)
    if held is None:
        # Only reached when the URL *looks* like Ollama. Unknown, not "no":
        # a client that renders an unreachable server as "nothing loaded"
        # invites a button press that then fails for an unrelated reason.
        if not _is_ollama(url):
            return info
        info["backend"] = "ollama"
        info["holding"] = HOLD_UNKNOWN
        info["detail"] = f"cannot reach {root} to ask what it is holding"
        return info

    info["backend"] = "ollama"
    ours = [m for m in held if _same_model(str(m.get("name") or ""), model)]
    info["holding"] = HOLD_YES if ours else HOLD_NO
    info["wake_s"] = 8.4
    if ours:
        # Only ours is reported and only ours is ever unloaded; the count of
        # other models is shown because it explains why memory does not drop
        # to zero, without naming somebody else's models.
        info["expires_at"] = ours[0].get("expires_at")
        info["size_bytes"] = ours[0].get("size")
    others = len(held) - len(ours)
    if others:
        info["others_held"] = others
    return info


def provider_is_local() -> bool:
    """Is the machine embedding through its own resident right now."""
    from . import settings

    return settings.provider() == "local"


def state() -> dict[str, Any]:
    """What the active backend is holding right now.

    Cheap and side-effect free by contract: it never loads a model and never
    embeds. Under `api` that matters for a second reason — a status call
    that embedded would cost money on a metered endpoint, the same reasoning
    that keeps `ApiProvider.health()` from making a request.
    """
    from . import settings

    provider = settings.provider()
    if provider == "local":
        return _describe_local()
    return _describe_api()


def unload() -> dict[str, Any]:
    """Give the memory back. Returns the state as re-read afterwards.

    Re-read rather than assumed, for the same reason `/api/autostart` does
    it: the verdict that matters is where the machine ended up, and a caller
    told "unloaded" by an optimistic reply has no way to discover otherwise.
    """
    from . import settings

    provider = settings.provider()
    if provider == "local":
        from . import service_ctl

        outcome = service_ctl.stop_resident()
        if outcome == service_ctl.RESIDENT_FOREIGN:
            raise EmbedControlUnavailable(
                "something else is listening on the resident's port and does "
                "not answer our token — refusing to stop a process that is "
                "not ours"
            )
        if outcome == service_ctl.RESIDENT_FAILED:
            raise EmbedControlUnavailable(
                "the resident did not exit; it may be mid-embed — try again "
                "in a moment"
            )
        # RESIDENT_ABSENT is success: the memory is already free, and
        # reporting "nothing to do" as a failure would make the button lie
        # about an outcome the user asked for and already has.
        return state()

    url = settings.api_url()
    root = _ollama_root(url)
    if root is None or not _is_ollama(url):
        raise EmbedControlUnavailable(
            "this endpoint holds nothing on this machine — there is nothing "
            "to unload"
        )

    import httpx

    model = settings.api_model()
    try:
        # The NATIVE endpoint, deliberately: /v1/embeddings accepts
        # `keep_alive` and ignores it (measured), so the OpenAI-compatible
        # path would report success and free nothing.
        resp = httpx.post(
            f"{root}/api/embed",
            json={"model": model, "input": "", "keep_alive": 0},
            timeout=settings.api_timeout(),
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise EmbedControlUnavailable(
            f"could not ask {root} to release {model}: {exc}"
        ) from exc
    return state()


def load() -> dict[str, Any]:
    """Bring the model back, and prove it works while doing so.

    A probe embedding rather than a bare "start the process": the useful
    question here is not whether something is running but whether this
    machine can produce a vector, and one call answers both. Under `api`
    this is also the endpoint check that has been missing — a wrong URL is
    otherwise discovered only when a bank starts rebuilding.

    Never downloads: `get_provider()` raises `EmbeddingUnavailable` when the
    local model is not cached, which keeps the explicit-warmup invariant
    (design §1) intact on a path a button can reach.
    """
    from .providers import EmbeddingUnavailable, get_provider

    try:
        provider = get_provider()
        vector = provider.embed_query("mnemo")
    except EmbeddingUnavailable as exc:
        raise EmbedControlUnavailable(str(exc)) from exc
    except ValueError as exc:
        # A misconfigured `api` provider (no url/model/dim) refuses at
        # construction; that is a settings problem, phrased as one.
        raise EmbedControlUnavailable(str(exc)) from exc

    width = len(vector)

    # Under `local`, a successful probe does NOT prove the resident is up:
    # `LocalProvider` falls back to embedding in-process when the resident is
    # not yet reachable, so the vector can arrive while the process that will
    # hold the model is still binding its port. Reading the state at that
    # instant reported «не завантажена» right after a load that said it
    # worked — the one contradiction this button must never show. Observed
    # live, not reasoned about.
    if provider_is_local():
        from .embed_server import server_is_up

        deadline = _time.monotonic() + 12.0
        while _time.monotonic() < deadline and not server_is_up():
            _time.sleep(0.25)

    out = state()
    out["probe_dim"] = width
    if width != provider.dim:
        # Not raised: the vector arrived, so the endpoint works — it is the
        # configured width that is wrong, and the index is what would suffer.
        # Naming it here is how it gets noticed before a rebuild writes it.
        out["detail"] = (
            f"the endpoint returned {width}-wide vectors but this machine is "
            f"configured for {provider.dim}; fix the width before indexing"
        )
    elif out.get("holding") == HOLD_NO:
        # The wait above expired. The embedding worked, so this is not a
        # failure — but a caller shown "worked" beside "not loaded" is owed
        # the reason, or the two read as a bug in the button.
        out["detail"] = (
            "the vector was computed in-process; the resident did not come "
            "up within the wait, so the next embed pays the wake-up again"
        )
    return out
