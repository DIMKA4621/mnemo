"""`GET /api/init-preview` (MN-62): what the add-bank dialog's «connect the
project (MCP)» would do, before the bank exists — routing, the bank-level
refusals, and that nothing is written or registered. The plan itself is
checked against a real `init` in test_platform.py.

    .venv/Scripts/python.exe tests/test_init_preview_api.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `mkdtemp`, not `TemporaryDirectory` — same Windows/servicelog reasoning as
# test_tree_prune.py.
_STATE = Path(tempfile.mkdtemp(prefix="mnemo init-preview-api state "))
os.environ["MNEMO_STATE_DIR"] = str(_STATE)
os.environ.pop("MNEMO_API_TOKEN", None)

from fastapi.testclient import TestClient  # noqa: E402

from src import api, registry  # noqa: E402

failures = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global failures
    failures += not ok
    line = ("PASS" if ok else "FAIL") + f": {label}" + (f" -- {detail}" if not ok else "")
    print(ascii(line)[1:-1])


def _preview(client: TestClient, root: Path | str, **params) -> tuple[int, dict]:
    resp = client.get("/api/init-preview", params={"root": str(root), **params})
    return resp.status_code, resp.json()


def main() -> int:
    client = TestClient(api.app)
    check("hidden from the schema",
          "/api/init-preview" not in client.get("/openapi.json").json()["paths"])

    with tempfile.TemporaryDirectory(prefix="mnemo init-preview-api proj ") as raw:
        proj = Path(raw) / "proj"
        bank_root = proj / ".claude" / "memory"
        proj.mkdir()

        code, body = _preview(client, "relative/.claude/memory")
        check("a relative root is 400 bad_request",
              code == 400 and body["error"]["code"] == "bad_request", body)

        code, body = _preview(client, proj)
        check("a root that is not <project>/.claude/memory is skipped",
              code == 200 and body["action"] == "skipped"
              and body["reason"] == "not_project_shaped"
              and body["project_root"] is None, body)

        code, body = _preview(client, bank_root)
        check("a missing bank root without create_structure is root_not_found",
              code == 200 and body["action"] == "refused"
              and body["reason"] == "root_not_found" and body["writes"] == [], body)

        code, body = _preview(client, bank_root, create_structure="true")
        check("with create_structure it is the full plan",
              code == 200 and body["action"] == "create"
              and body["project_root"] == str(proj.resolve())
              and body["bank_root"] == str(bank_root.resolve())
              and body["create_structure"] is True
              and ".claude/memory/MEMORY.md" in body["writes"]
              and ".mcp.env" in body["writes"], body)
        check("previewing created nothing",
              list(proj.iterdir()) == [], list(proj.iterdir()))
        check("and registered nothing", registry.load(force=True) == [])

        bank_root.mkdir(parents=True)
        code, body = _preview(client, bank_root)
        check("an existing bank root needs no create_structure",
              code == 200 and body["action"] == "create", body)

        registry.add(bank_root)
        code, body = _preview(client, bank_root)
        check("an already registered root is bank_exists",
              code == 200 and body["action"] == "refused"
              and body["reason"] == "bank_exists" and body["files"] == [], body)

        other = Path(raw) / "other"
        (other / ".claude" / "memory").mkdir(parents=True)
        (other / ".mcp.json.template").write_text("{nope", encoding="utf-8")
        code, body = _preview(client, other / ".claude" / "memory")
        check("a project-level refusal carries init's reason code",
              code == 200 and body["action"] == "refused"
              and body["reason"] == "invalid_json" and body["detail"], body)

        broken = Path(raw) / "broken"
        (broken / ".claude" / "memory").mkdir(parents=True)
        (broken / ".mcp.json").write_text('{"mcpServers": {,}}',
                                          encoding="utf-8")
        code, body = _preview(client, broken / ".claude" / "memory")
        check("an unparsable .mcp.json is an overridable refusal with a "
              "location",
              code == 200 and body["action"] == "refused"
              and body["reason"] == "invalid_json"
              and body["overridable"] is True
              and body["json_error"]["line"] == 1
              and body["json_error"]["column"] == 17, body)
        code, body = _preview(client, broken / ".claude" / "memory",
                              overwrite_invalid="true")
        files = {f["path"]: f for f in body.get("files", [])}
        check("overwrite_invalid=true previews the backup and the rebuild",
              code == 200 and body["action"] == "add"
              and body["overwrite_invalid"] is True
              and files.get(".mcp.json.bak", {}).get("reason") == "backup"
              and files[".mcp.json"]["action"] == "update", body)
        check("and still wrote nothing",
              sorted(p.name for p in broken.iterdir()) == [".claude",
                                                           ".mcp.json"])

        seen: dict = {}

        def fake_init(**kw):
            seen.update(kw)
            print("mnemo init: refused (fake)")
            return 1

        from src import scaffold
        original = scaffold.init_project
        scaffold.init_project = fake_init
        try:
            resp = client.post("/api/banks", json={
                "root": str(broken / ".claude" / "memory"), "init": True,
                "overwrite_invalid": True})
        finally:
            scaffold.init_project = original
        check("POST /api/banks hands overwrite_invalid to init",
              resp.status_code == 201 and seen.get("overwrite_invalid") is True
              and resp.json()["init"]["ok"] is False, (resp.text, seen))

    print(f"\n{'FAILED' if failures else 'OK'}: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
