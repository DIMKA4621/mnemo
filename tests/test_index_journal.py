"""A bulk scan that found nothing must not leave a journal row; one that found
changes, a rebuild, a file and any failure still do.

    .venv/bin/python tests/test_index_journal.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `mkdtemp`, not `TemporaryDirectory` — same Windows/servicelog reasoning as
# test_tree_prune.py.
os.environ["MNEMO_STATE_DIR"] = tempfile.mkdtemp(prefix="mnemo index-journal state ")

from src import api, servicelog  # noqa: E402


def _done(kind: str, **data) -> dict:
    return {"type": "index_done", "bank_id": "b1",
            "data": {"kind": kind, "trigger": "watcher", "result": "ok", **data}}


def main() -> int:
    api._on_queue_event(_done("bulk", path=None, planned_files=0, planned_prunes=0))
    api._on_queue_event(_done("bulk", path=None, result="skipped"))
    api._on_queue_event(_done("bulk", path=None, planned_files=2, planned_prunes=0))
    api._on_queue_event(_done("rebuild", path=None, planned_files=0, planned_prunes=0))
    api._on_queue_event(_done("file", path="a.md", files_indexed=1))
    api._on_queue_event({"type": "index_error", "bank_id": "b1",
                         "data": {"kind": "bulk", "trigger": "watcher", "error": "boom"}})

    rows = servicelog.read_index(limit=100)
    got = sorted((r["kind"], r["result"]) for r in rows)
    want = sorted([("bulk", "ok"), ("rebuild", "ok"), ("file", "ok"), ("bulk", "error")])
    ok = got == want
    print(("PASS" if ok else "FAIL") + f": journal rows {got}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
