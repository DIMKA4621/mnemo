"""Telemetry log for the UserPromptSubmit auto-inject path.

One JSONL line per ``mnemo hook-inject`` call — ok, skipped or errored
alike — so MIN_SIM / INJECT_TOP_N / gate behaviour can be tuned from
real data.

Per-project: each project gets its own ``state/logs/<projhash>.log``
keyed by the same hash as its index DB (so log and index sit side by
side and you never have to grep-filter a global stream). When the cwd
is unknown — bad payload, parse failure — entries go to
``state/logs/_unknown.log``.

Rotation caps disk use per project; see ``config.INJECT_LOG_*``.
Best-effort: any logging failure is swallowed — telemetry must NEVER
break or slow the hook itself.
"""
from __future__ import annotations

import json
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import (
    INJECT_LOG_BACKUPS,
    INJECT_LOG_DIR,
    INJECT_LOG_MAX_BYTES,
    INJECT_LOG_PROMPT_CHARS,
    resolve,
)

# One logger per resolved log file path — keep handlers cached so we do
# not reopen the file on every call. Process-lifetime cache; the hook
# process is short-lived anyway.
_loggers: dict[str, logging.Logger] = {}


def _log_path_for(cwd: str | None) -> Path:
    """Resolve the per-project log file. Falls back to ``_unknown.log``
    when ``cwd`` is missing or unresolvable."""
    if cwd:
        try:
            return INJECT_LOG_DIR / f"{resolve(cwd).db.stem}.log"
        except (OSError, ValueError):
            pass
    return INJECT_LOG_DIR / "_unknown.log"


def _get_logger(path: Path) -> logging.Logger | None:
    key = str(path)
    cached = _loggers.get(key)
    if cached is not None:
        return cached
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=INJECT_LOG_MAX_BYTES,
            backupCount=INJECT_LOG_BACKUPS,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        lg = logging.getLogger(f"mnemo.inject.{path.stem}")
        lg.setLevel(logging.INFO)
        lg.propagate = False
        if not lg.handlers:
            lg.addHandler(handler)
        _loggers[key] = lg
        return lg
    except OSError:
        return None


def log_inject(
    *,
    status: str,
    cwd: str | None,
    prompt: str,
    total_ms: float,
    embed_ms: float | None = None,
    search_ms: float | None = None,
    hits: list[dict[str, Any]] | None = None,
    note: str | None = None,
) -> None:
    """Write one JSONL line to the per-project log. Never raises."""
    try:
        lg = _get_logger(_log_path_for(cwd))
        if lg is None:
            return
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            "status": status,
            "cwd": cwd,
            "prompt": (prompt or "")[:INJECT_LOG_PROMPT_CHARS],
            "total_ms": round(total_ms, 1),
            "embed_ms": None if embed_ms is None else round(embed_ms, 1),
            "search_ms": None if search_ms is None else round(search_ms, 1),
            "hits": hits or [],
            "note": note,
        }
        lg.info(json.dumps(record, ensure_ascii=False))
    except Exception:
        # Telemetry MUST NOT break the hook.
        return
