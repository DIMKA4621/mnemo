"""MN-11: watcher fast-retry unit coverage.

Three things this covers, none of which had a single test before this file
existed:

1. ``_is_embedding_unavailable_error`` — the classifier that decides whether
   an ``index_events`` row was an ``EmbeddingUnavailable`` (retry it) or a
   deterministic failure (never retry it), against real message shapes from
   every ``raise EmbeddingUnavailable(...)`` site in ``src/providers/`` and
   ``src/embed_server.py``, and against the generic
   ``f"{type(exc).__name__}: {exc}"`` shape ``workqueue._execute`` uses for
   everything else.
2. ``_embedding_unavailable_streak`` — against a real (temp) ``service.db``,
   fabricated with ``servicelog.log_index``, including the case the scan-
   window bug hid: a streak longer than the old fixed 50-event window, with
   ``MNEMO_RETRY_MAX_ATTEMPTS`` configured above 50.
3. The admission math in ``_retry_failed_banks`` (``0 < streak <
   max_attempts`` fires, ``streak == 0`` and ``streak >= max_attempts`` do
   not) — against a mocked registry and a mocked ``_rescan_bank``, so it is
   exercised without a real bank, a real queue, or a running watcher thread.

No model and no embedding resident are touched here — the streak scan reads
``service.db`` directly, and the admission test mocks the one function that
would otherwise touch the queue. So this file skips the private-embed-port
dance ``tests/_hygiene.py`` exists for (see its docstring): nothing here
spawns a process for that dance to guard.

    .venv/bin/python tests/test_watcher.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `config` reads the environment at import time, so the redirect has to
# happen before anything imports it — same reasoning as test_tree_prune.py.
#
# `mkdtemp`, not `TemporaryDirectory`: this file (unlike test_tree_prune.py)
# opens `service.db` through `servicelog`, which caches a per-thread READER
# connection (`servicelog._reader`) that `servicelog.close()` does not close.
# A `TemporaryDirectory`'s exit-time auto-cleanup then races that still-open
# handle and Windows refuses the unlink. Same convention as
# test_service_ctl.py / test_service_recovery.py, which hold state for the
# same reason and never auto-clean it either.
_STATE = tempfile.mkdtemp(prefix="mnemo watcher state ")
os.environ["MNEMO_STATE_DIR"] = _STATE

from src import config, servicelog, watcher  # noqa: E402
from src.providers.local import _UNAVAILABLE  # noqa: E402

_passed = 0
_failed = 0

BANK = "0123456789abcdef"


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def _clear_events() -> None:
    conn = servicelog.connect()
    conn.execute("DELETE FROM index_events")
    conn.commit()


def _fabricate(bank_id: str, entries: list[tuple[str, str | None]]) -> None:
    """Insert ``index_events`` rows, oldest first, kind always ``file``.

    Each entry is ``(result, error)``; a ``result != "error"`` row is a
    success and its ``error`` is ignored — matching what ``workqueue``
    actually writes (``log_index`` allows ``error=None`` on success).
    """
    for result, error in entries:
        servicelog.log_index(
            bank_id=bank_id, kind="file", trigger="watcher", path="x.md",
            result=result, files_indexed=1 if result != "error" else 0,
            error=error,
        )


# ------------------------------------------------------ classifier (item 1)


def test_is_embedding_unavailable_error() -> None:
    print("\n=== _is_embedding_unavailable_error ===")

    unreachable_url = "http://127.0.0.1:4645"
    embedding_unavailable_cases = {
        # src/providers/local.py — no local model cached either
        "local: resident+cache both unavailable": _UNAVAILABLE,
        # src/providers/local.py — a short vector list from the resident
        "local: short vector list":
            "provider returned 3 vectors for 5 texts",
        # src/embed_server.py — a live resident that refused our token
        "resident: token refused":
            f"the embedding resident at 127.0.0.1:4645 refused our token "
            f"(read from C:\\Users\\x\\.mnemo\\embed.token). It is running "
            f"with a different secret — restart it, or point "
            f"MNEMO_EMBED_TOKEN_FILE at the one it used. NOT falling back "
            f"to an in-process model: that would load a second ~2.2 GB "
            f"copy beside a working resident.",
        # src/providers/api.py — HTTP error status from the endpoint
        "api: http status error":
            f"{unreachable_url} returned 500: internal server error",
        # src/providers/api.py — endpoint unreachable
        "api: unreachable":
            f"cannot reach the embedding endpoint {unreachable_url}: "
            f"Connection refused",
        # src/providers/api.py — response shape mismatch
        "api: unexpected shape":
            f'{unreachable_url} answered in an unexpected shape; expected '
            '{"data": [{"embedding": [...]}]}, got {"error": "bad request"}',
        # src/providers/api.py — dimension mismatch
        "api: dim mismatch":
            "endpoint returned 512-dim vectors but the configured dim is "
            "1024; the index column cannot hold these",
    }
    for name, message in embedding_unavailable_cases.items():
        check(f"classified retryable: {name}",
              watcher._is_embedding_unavailable_error(message) is True,
              detail=message)

    # workqueue._execute's OWN formatting for every non-EmbeddingUnavailable
    # exception: f"{type(exc).__name__}: {exc}" — this is the one signal
    # that must never be mistaken for a retryable failure.
    generic_cases = {
        "ValueError": "ValueError: dimension mismatch in chunk 4",
        "KeyError": "KeyError: 'sha256'",
        "RuntimeError, colon in payload":
            "RuntimeError: could not parse: unexpected token",
        "OSError": "OSError: [Errno 2] No such file or directory: 'x.md'",
    }
    for name, message in generic_cases.items():
        check(f"classified NOT retryable: {name}",
              watcher._is_embedding_unavailable_error(message) is False,
              detail=message)

    check("None is not retryable",
          watcher._is_embedding_unavailable_error(None) is False)
    check("empty string is not retryable",
          watcher._is_embedding_unavailable_error("") is False)


# --------------------------------------------------------- streak (item 2)


def test_embedding_unavailable_streak() -> None:
    print("\n=== _embedding_unavailable_streak ===")
    servicelog.connect()

    _clear_events()
    check("empty history -> streak 0",
          watcher._embedding_unavailable_streak(BANK) == 0)

    _clear_events()
    _fabricate(BANK, [
        ("error", _UNAVAILABLE),
        ("error", "provider returned 3 vectors for 5 texts"),
        ("error", _UNAVAILABLE),
    ])
    check("pure unbroken streak -> counts every row",
          watcher._embedding_unavailable_streak(BANK) == 3)

    _clear_events()
    _fabricate(BANK, [
        ("error", _UNAVAILABLE),
        ("error", _UNAVAILABLE),
        ("ok", None),  # newest: the provider is working again
    ])
    check("a trailing success closes the streak at 0",
          watcher._embedding_unavailable_streak(BANK) == 0)

    _clear_events()
    _fabricate(BANK, [
        ("error", _UNAVAILABLE),
        ("error", _UNAVAILABLE),
        ("error", "ValueError: dimension mismatch"),  # newest: deterministic
    ])
    check("a leading deterministic error closes the streak at 0 "
          "(never retry a deterministic failure)",
          watcher._embedding_unavailable_streak(BANK) == 0)

    # A deterministic error BEHIND the recent failures must not be counted
    # past — the streak is "since the last thing that was not an
    # EmbeddingUnavailable error", scanning from the newest row backwards.
    _clear_events()
    _fabricate(BANK, [
        ("error", "KeyError: 'sha256'"),          # oldest: deterministic
        ("error", _UNAVAILABLE),
        ("error", _UNAVAILABLE),
    ])
    check("only the run since the deterministic error counts",
          watcher._embedding_unavailable_streak(BANK) == 2)

    # The bug this ticket exists to fix: with MNEMO_RETRY_MAX_ATTEMPTS
    # configured above the old fixed 50-row scan window, a streak past 50
    # must still be observed, or the retry admission below can never see it
    # reach the cap.
    _clear_events()
    with patch.object(config, "RETRY_MAX_ATTEMPTS", 200):
        check("scan window follows the configured cap (200 -> >= 210)",
              watcher._retry_scan_limit() >= 210,
              detail=str(watcher._retry_scan_limit()))
        _fabricate(BANK, [("error", _UNAVAILABLE) for _ in range(120)])
        streak = watcher._embedding_unavailable_streak(BANK)
        check("a 120-long streak is fully observed above the old 50-row cap",
              streak == 120, detail=str(streak))

    # And the shipped default (5) keeps the floor generous — nothing here
    # regresses the common case just to fix the configurable one. Pinned
    # explicitly rather than trusting the ambient environment, so a stray
    # MNEMO_RETRY_MAX_ATTEMPTS in whoever's shell runs this can't flake it.
    with patch.object(config, "RETRY_MAX_ATTEMPTS", 5):
        check("default cap (5) still uses the floor, not a shrunk window",
              watcher._retry_scan_limit() == watcher._RETRY_SCAN_LIMIT_FLOOR,
              detail=str(watcher._retry_scan_limit()))

    _clear_events()


# --------------------------------------------------- admission math (item 3)


def test_retry_admission_math() -> None:
    """``_retry_failed_banks``: fires only for ``0 < streak < max_attempts``.

    ``registry.load`` and ``_embedding_unavailable_streak`` are mocked so
    this is pure arithmetic over the admission rule — no real bank, no real
    queue, no running watcher thread.
    """
    print("\n=== retry admission math ===")

    class _FakeBank:
        def __init__(self, id_: str) -> None:
            self.id = id_
            self.watched = True
            self.exists = True

    bank = _FakeBank(BANK)
    calls: list[str] = []

    def fake_rescan(bank_id: str, *, trigger: str) -> None:
        calls.append(bank_id)

    def run(streak: int, max_attempts: int) -> list[str]:
        calls.clear()
        with patch.object(watcher.registry, "load", return_value=[bank]), \
             patch.object(watcher, "_embedding_unavailable_streak",
                          return_value=streak), \
             patch.object(watcher, "_rescan_bank", side_effect=fake_rescan), \
             patch.object(config, "RETRY_MAX_ATTEMPTS", max_attempts):
            watcher._retry_failed_banks()
        return list(calls)

    check("streak == 0 -> no retry (nothing to recover from)",
          run(streak=0, max_attempts=5) == [])
    check("0 < streak < max_attempts -> retry fires",
          run(streak=3, max_attempts=5) == [BANK])
    check("streak == max_attempts -> no retry (cap reached)",
          run(streak=5, max_attempts=5) == [])
    check("streak > max_attempts -> no retry (cap already exceeded)",
          run(streak=9, max_attempts=5) == [])
    check("max_attempts == 0 -> retrying is fully disabled",
          run(streak=3, max_attempts=0) == [])
    # The scenario the scan-window bug produced: a cap set well above the
    # old fixed 50-row window. Admission math itself was always correct —
    # this confirms it stays correct once the streak it is given can
    # actually reach that high.
    check("a streak just under a >50 cap still retries",
          run(streak=119, max_attempts=120) == [BANK])
    check("a streak at a >50 cap stops retrying",
          run(streak=120, max_attempts=120) == [])

    # A bank the watcher does not follow (frozen/disabled, or its root is
    # gone) must never be probed at all — `_embedding_unavailable_streak`
    # should not even be called for it.
    unwatched = _FakeBank(BANK)
    unwatched.watched = False
    with patch.object(watcher.registry, "load", return_value=[unwatched]), \
         patch.object(watcher, "_embedding_unavailable_streak") as streak_fn, \
         patch.object(watcher, "_rescan_bank", side_effect=fake_rescan), \
         patch.object(config, "RETRY_MAX_ATTEMPTS", 5):
        calls.clear()
        watcher._retry_failed_banks()
        check("an unwatched bank is skipped before the streak is even read",
              not streak_fn.called and calls == [])


def main() -> int:
    try:
        test_is_embedding_unavailable_error()
        test_embedding_unavailable_streak()
        test_retry_admission_math()
    finally:
        servicelog.close()

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
