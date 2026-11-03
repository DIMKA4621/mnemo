"""`GET /api/logs?q=` (MN-54): a literal, Unicode case-insensitive substring
filter on the query text (kind=query) or the path (kind=index), applied to
both the page and `total`.

    .venv/Scripts/python.exe tests/test_logs_filter.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `mkdtemp`, not `TemporaryDirectory` — same Windows/servicelog reasoning as
# test_tree_prune.py.
os.environ["MNEMO_STATE_DIR"] = tempfile.mkdtemp(prefix="mnemo logs-filter state ")
os.environ.pop("MNEMO_API_TOKEN", None)

from fastapi.testclient import TestClient  # noqa: E402

from src import api, servicelog  # noqa: E402

QUERIES = [
    "Як влаштована ЧЕРГА",
    "черга і воркер",
    "watcher debounce",
    "100% coverage",
    "1000 coverage",
    "snake_case name",
    "snakeXcase name",
    r"C:\work\mnemo",
]
PATHS = ["topics/Черга.md", "logs/2026-11-02.md", None]

failures = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global failures
    failures += not ok
    line = ("PASS" if ok else "FAIL") + f": {label}" + (f" -- {detail}" if not ok else "")
    print(ascii(line)[1:-1])  # the Windows console is cp1252


def _texts(body: dict, field: str) -> list[str | None]:
    return sorted((e[field] for e in body["events"]), key=str)


def _queries(q: str | None, **kw) -> dict:
    return api.api_logs(kind="query", q=q, limit=kw.get("limit", 1000),
                        offset=kw.get("offset", 0))


def main() -> int:
    for text in QUERIES:
        servicelog.log_query(bank_id="b1", face="mcp", query=text, path_prefix=None,
                             status="ready", hits=[], took_ms=1.0)
    for path in PATHS:
        servicelog.log_index(bank_id="b1", kind="file" if path else "bulk",
                             trigger="watcher", path=path, result="ok")

    body = _queries("черга")
    check("Cyrillic case-insensitive (upper matches lower)",
          _texts(body, "query") == sorted(["Як влаштована ЧЕРГА", "черга і воркер"]),
          _texts(body, "query"))
    check("total follows the filter", body["total"] == 2, body["total"])

    body = _queries("  ЧеРгА  ")
    check("trimmed, mixed case", body["total"] == 2, body["total"])

    body = _queries("WATCHER")
    check("Latin case-insensitive", _texts(body, "query") == ["watcher debounce"],
          _texts(body, "query"))

    body = _queries("0%")
    check("% is literal", _texts(body, "query") == ["100% coverage"], _texts(body, "query"))
    body = _queries("e_c")
    check("_ is literal", _texts(body, "query") == ["snake_case name"], _texts(body, "query"))
    body = _queries("\\work")
    check("backslash is literal", _texts(body, "query") == [r"C:\work\mnemo"],
          _texts(body, "query"))

    for empty in (None, "", "   "):
        body = _queries(empty)
        check(f"q={empty!r} means no filter", body["total"] == len(QUERIES), body["total"])

    body = _queries("coverage", limit=1, offset=1)
    check("pagination: total is the filtered count, page is one row",
          body["total"] == 2 and len(body["events"]) == 1, body)

    body = api.api_logs(kind="index", q="черга", limit=1000, offset=0)
    check("kind=index filters by path, case-insensitive",
          _texts(body, "path") == ["topics/Черга.md"] and body["total"] == 1, body)
    body = api.api_logs(kind="index", limit=1000, offset=0)
    check("kind=index without q keeps path-less bulk rows", body["total"] == 3, body["total"])

    # The admin MCP `logs` tool's call shape: no `q` at all.
    body = api.api_logs(kind="query", bank=None, limit=200, offset=0)
    check("internal caller without q still works", body["total"] == len(QUERIES), body["total"])

    resp = TestClient(api.app).get("/api/logs", params={"kind": "query", "q": "ЧЕРГА"})
    check("HTTP: q arrives as a query parameter",
          resp.status_code == 200 and resp.json()["total"] == 2,
          f"{resp.status_code} {resp.text[:200]}")

    print("ALL PASS" if not failures else f"{failures} FAILED")
    return 0 if not failures else 1


def test_logs_filter() -> None:
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
