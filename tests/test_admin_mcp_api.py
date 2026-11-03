"""`/api/admin-mcp` (MN-27): the admin MCP entry for the console, and wiring
it into a chosen project — routing, status codes, and the lazily minted
service token. The file-writing rules themselves are in test_platform.py.

    .venv/Scripts/python.exe tests/test_admin_mcp_api.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `mkdtemp`, not `TemporaryDirectory` — same Windows/servicelog reasoning as
# test_tree_prune.py.
_STATE = Path(tempfile.mkdtemp(prefix="mnemo admin-mcp-api state "))
os.environ["MNEMO_STATE_DIR"] = str(_STATE)
os.environ.pop("MNEMO_API_TOKEN", None)

from fastapi.testclient import TestClient  # noqa: E402

from src import api  # noqa: E402

failures = 0


def check(label: str, ok: bool, detail: object = "") -> None:
    global failures
    failures += not ok
    line = ("PASS" if ok else "FAIL") + f": {label}" + (f" -- {detail}" if not ok else "")
    print(ascii(line)[1:-1])


def main() -> int:
    client = TestClient(api.app)
    token_file = _STATE / "api.token"
    check("no service token before the first admin-mcp call", not token_file.exists())

    resp = client.get("/api/admin-mcp")
    body = resp.json()
    check("GET /api/admin-mcp is 200", resp.status_code == 200, resp.text)
    check("it mints the service token lazily", token_file.exists())
    token = token_file.read_text(encoding="utf-8").strip()
    check("the payload carries that token", body.get("token") == token, body)
    check("entry_name is mnemo-admin", body.get("entry_name") == "mnemo-admin", body)
    check("url is the literal admin url",
          body.get("url", "").endswith(f"/mcp-admin?token={token}"), body)
    check("snippet has both forms",
          set(body.get("snippet", {})) == {"literal", "template"}, body)
    check("hidden from the schema",
          not any(p.startswith("/api/admin-mcp") for p in client.get("/openapi.json").json()["paths"]))

    with tempfile.TemporaryDirectory(prefix="mnemo admin-mcp-api proj ") as raw:
        proj = Path(raw)
        resp = client.get("/api/admin-mcp/wiring", params={"root": str(proj)})
        pre = resp.json()
        check("preview is 200 and 'create'",
              resp.status_code == 200 and pre.get("action") == "create", resp.text)
        check("preview writes nothing", not (proj / ".mcp.json").exists())

        resp = client.get("/api/admin-mcp/wiring", params={"root": "relative/dir"})
        check("relative root is 400 bad_request",
              resp.status_code == 400 and resp.json()["error"]["code"] == "bad_request",
              resp.text)
        resp = client.get("/api/admin-mcp/wiring", params={"root": str(proj / "nope")})
        check("missing root previews as not_found",
              resp.status_code == 200 and resp.json()["reason"] == "not_found", resp.text)

        resp = client.post("/api/admin-mcp/wiring", json={"root": str(proj)})
        done = resp.json()
        check("POST wires it", resp.status_code == 200
              and ".mcp.json" in done.get("written", []), resp.text)
        servers = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        check("the written entry carries the service token",
              servers["mnemo-admin"]["url"].endswith(f"?token={token}"), servers)
        check("POST returns the after-preview",
              done.get("preview", {}).get("action") == "already", done)

        resp = client.post("/api/admin-mcp/wiring", json={"root": str(proj)})
        check("POST again is 200 and writes nothing",
              resp.status_code == 200 and resp.json()["written"] == [], resp.text)

        resp = client.post("/api/admin-mcp/wiring", json={"root": str(proj / "nope")})
        check("POST to a missing root is 400 root_not_found",
              resp.status_code == 400
              and resp.json()["error"]["code"] == "root_not_found", resp.text)

        resp = client.get("/api/admin-mcp/wiring",
                          params={"root": str(proj), "mode": "both"})
        check("an unknown mode is 400 bad_request",
              resp.status_code == 400 and resp.json()["error"]["code"] == "bad_request",
              resp.text)

        tpl = proj / "tpl"
        tpl.mkdir()
        resp = client.get("/api/admin-mcp/wiring", params={"root": str(tpl), "mode": "template"})
        check("template preview echoes the mode",
              resp.json().get("mode") == "template"
              and resp.json().get("suggested_mode") == "literal", resp.text)
        resp = client.post("/api/admin-mcp/wiring", json={"root": str(tpl), "mode": "template"})
        tdone = resp.json()
        check("POST template builds the layer",
              resp.status_code == 200 and ".mcp.json.template" in tdone.get("written", [])
              and f"MNEMO_ADMIN_TOKEN={token}" in (tpl / ".mcp.env").read_text(encoding="utf-8"),
              resp.text)
        check("the template itself holds no token",
              token not in (tpl / ".mcp.json.template").read_text(encoding="utf-8"))

        bad = proj / "bad"
        bad.mkdir()
        (bad / ".mcp.json").write_text("[1, 2]", encoding="utf-8")
        resp = client.post("/api/admin-mcp/wiring", json={"root": str(bad)})
        err = resp.json().get("error", {})
        check("POST on a non-object .mcp.json is 409 mcp_config_invalid",
              resp.status_code == 409 and err.get("code") == "mcp_config_invalid", resp.text)
        check("the refusal carries the preview",
              err.get("detail", {}).get("preview", {}).get("reason") == "invalid_json", err)

    print(f"\n{'FAILED' if failures else 'OK'}: {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
