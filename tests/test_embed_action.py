"""MN-20: a busy workqueue must not refuse `/api/embed/load`.

`_embed_action`'s queue-depth guard exists because the worker embeds through
the same backend `unload` would pull memory out from under — genuinely
unsafe mid-index. `load` is a probe embed on `embed_server.py`'s own
`_QUERY_LANE`, isolated from the worker's `_BATCH_LANE`, so it never
actually queues behind bulk work and has nothing to be refused over.

No model, no network, no live service — `api.api_embed_load` /
`api.api_embed_unload` are plain functions once imported, and the queue
snapshot they check is monkeypatched to a simulated busy state rather than
read from a real queue.

    .venv/bin/python tests/test_embed_action.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `config` reads the environment at import time, so the redirect has to
# happen before anything imports it — same reasoning as test_pipeline.py.
#
# `mkdtemp`, not `TemporaryDirectory`: importing `src.api` opens `service.db`
# through `servicelog`, which caches a per-thread READER connection
# `servicelog.close()` does not close, so a `TemporaryDirectory`'s exit-time
# auto-cleanup races that still-open handle and Windows refuses the unlink.
# Same convention as test_watcher.py / test_tree_prune.py.
_STATE = tempfile.mkdtemp(prefix="mnemo embed-action state ")
os.environ["MNEMO_STATE_DIR"] = _STATE

from src import api, embedctl  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


_BUSY_DEPTH = {"depth": 3, "high": 0, "normal": 3, "low": 0,
               "current": None, "by_bank": {}}
_BUSY_CURRENT = {"depth": 0, "high": 0, "normal": 0, "low": 0,
                  "current": {"bank": "x", "path": "a.md"}, "by_bank": {}}


class _Patch:
    """Swap `api._queue_snapshot_json` / `embedctl.load` / `embedctl.unload`
    for the duration of one test, then restore them."""

    def __init__(self, **replacements: object) -> None:
        self._replacements = replacements
        self._saved: dict[str, object] = {}

    def __enter__(self) -> "_Patch":
        for name, value in self._replacements.items():
            module, attr = name.split(".", 1)
            target = api if module == "api" else embedctl
            self._saved[name] = getattr(target, attr)
            setattr(target, attr, value)
        return self

    def __exit__(self, *exc: object) -> None:
        for name, value in self._saved.items():
            module, attr = name.split(".", 1)
            target = api if module == "api" else embedctl
            setattr(target, attr, value)


def test_load_ignores_busy_queue_depth() -> None:
    calls = {"n": 0}

    def fake_load() -> dict:
        calls["n"] += 1
        return {"holding": "loaded", "probe_dim": 1024}

    with _Patch(**{"api._queue_snapshot_json": lambda: dict(_BUSY_DEPTH),
                    "embedctl.load": fake_load}):
        result = api.api_embed_load()

    check("api_embed_load succeeds while the queue has depth", calls["n"] == 1)
    check("api_embed_load returns the probe result",
          result == {"holding": "loaded", "probe_dim": 1024}, detail=str(result))


def test_load_ignores_busy_current_task() -> None:
    calls = {"n": 0}

    def fake_load() -> dict:
        calls["n"] += 1
        return {"holding": "loaded", "probe_dim": 1024}

    with _Patch(**{"api._queue_snapshot_json": lambda: dict(_BUSY_CURRENT),
                    "embedctl.load": fake_load}):
        result = api.api_embed_load()

    check("api_embed_load succeeds while a file is in flight", calls["n"] == 1,
          detail=str(result))


def test_unload_still_refuses_busy_depth() -> None:
    calls = {"n": 0}

    def fake_unload() -> dict:
        calls["n"] += 1
        return {"holding": "unloaded"}

    with _Patch(**{"api._queue_snapshot_json": lambda: dict(_BUSY_DEPTH),
                    "embedctl.unload": fake_unload}):
        try:
            api.api_embed_unload()
        except api.ApiError as exc:
            check("api_embed_unload refuses with embed_busy",
                  exc.code == "embed_busy", detail=exc.code)
            check("the refusal cites the pending depth",
                  exc.detail is not None and exc.detail.get("depth") == 3,
                  detail=str(exc.detail))
        else:
            check("api_embed_unload refuses while the queue has depth", False,
                  detail="did not raise")
    check("unload() itself was never called", calls["n"] == 0)


def test_unload_still_refuses_busy_current() -> None:
    calls = {"n": 0}

    def fake_unload() -> dict:
        calls["n"] += 1
        return {"holding": "unloaded"}

    with _Patch(**{"api._queue_snapshot_json": lambda: dict(_BUSY_CURRENT),
                    "embedctl.unload": fake_unload}):
        try:
            api.api_embed_unload()
        except api.ApiError as exc:
            check("api_embed_unload refuses with embed_busy (in-flight task)",
                  exc.code == "embed_busy", detail=exc.code)
        else:
            check("api_embed_unload refuses while a file is in flight", False,
                  detail="did not raise")
    check("unload() itself was never called (in-flight task)", calls["n"] == 0)


def test_unload_proceeds_when_queue_idle() -> None:
    calls = {"n": 0}

    def fake_unload() -> dict:
        calls["n"] += 1
        return {"holding": "unloaded"}

    with _Patch(**{"api._queue_snapshot_json": lambda: dict(api._EMPTY_QUEUE),
                    "embedctl.unload": fake_unload}):
        result = api.api_embed_unload()

    check("api_embed_unload proceeds once the queue is idle", calls["n"] == 1)
    check("api_embed_unload returns the unload result",
          result == {"holding": "unloaded"}, detail=str(result))


def main() -> int:
    test_load_ignores_busy_queue_depth()
    test_load_ignores_busy_current_task()
    test_unload_still_refuses_busy_depth()
    test_unload_still_refuses_busy_current()
    test_unload_proceeds_when_queue_idle()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
