"""MN-13: MCP-wiring detection + strip on bank removal.

No model, no network, no live service — `project_mcp_wiring`/`strip_mcp_
wiring` are plain filesystem/JSON functions, and `api_remove_bank` never
touches the index for a bank that was never indexed (`_bank_conn`
short-circuits to `None`, same as `test_tree_prune.py`).

    .venv/bin/python tests/test_mn13_mcp_wiring.py
"""
from __future__ import annotations

import json
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
# `mkdtemp`, not `TemporaryDirectory`: `api.api_remove_bank` opens
# `service.db` through `servicelog`, which caches a per-thread READER
# connection `servicelog.close()` does not close — a `TemporaryDirectory`'s
# exit-time auto-cleanup would race that still-open handle on Windows. Same
# convention as test_watcher.py / test_tree_prune.py.
_STATE = tempfile.mkdtemp(prefix="mnemo mn13-mcp-wiring state ")
os.environ["MNEMO_STATE_DIR"] = _STATE

from src import api, registry, scaffold  # noqa: E402

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


def _make_project(tmp: Path) -> tuple[Path, Path]:
    """`<tmp>/proj` with a `.claude/memory` bank root already created."""
    proj = tmp / "proj"
    memory = proj / ".claude" / "memory"
    memory.mkdir(parents=True)
    return proj, memory


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _bank_exists(bank_id: str) -> bool:
    try:
        registry.get(bank_id)
        return True
    except registry.BankNotFound:
        return False


# --------------------------------------------------- not a project-shaped bank


def test_non_project_bank_reports_no_wiring() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo mn13 nonproj ") as tmp:
        root = Path(tmp) / "bare-bank"
        root.mkdir()
        bank = registry.add(root, name="mn13-nonproject")

        info = api._mcp_wiring_info(bank)
        check("no wiring for a non .claude/memory bank root",
              info == {"has_wiring": False, "uses_template": False,
                       "project_root": None},
              detail=str(info))

        try:
            api.api_remove_bank(bank.id, drop_index=False, strip_mcp=True)
            check("strip_mcp on a non-project bank is rejected", False,
                  detail="did not raise")
        except api.ApiError as exc:
            check("strip_mcp on a non-project bank is rejected",
                  exc.code == "bad_request", detail=exc.code)
        # Bank must still be registered — the rejection happens before any
        # removal work starts.
        check("bank survives the rejected removal", _bank_exists(bank.id))


# ----------------------------------------------------- project with no wiring


def test_project_with_no_mcp_files_reports_no_wiring() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo mn13 nowiring ") as tmp:
        proj, memory = _make_project(Path(tmp))
        bank = registry.add(memory, name="mn13-nowiring")

        status = scaffold.project_mcp_wiring(proj)
        check("no .mcp.json/.mcp.json.template -> has_wiring False",
              status == {"has_wiring": False, "uses_template": False},
              detail=str(status))

        info = api._mcp_wiring_info(bank)
        check("api reports the same via bank root",
              info["has_wiring"] is False and info["project_root"] == str(proj),
              detail=str(info))


# ------------------------------------------------------- direct .mcp.json


def test_direct_mcp_json_detected_and_stripped() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo mn13 direct ") as tmp:
        proj, memory = _make_project(Path(tmp))
        bank = registry.add(memory, name="mn13-direct")
        token = registry.token_for(bank.id)

        mcp_json = proj / ".mcp.json"
        _write_json(mcp_json, {
            "mcpServers": {
                scaffold._INSTANCE: scaffold._mcp_server(token),
                "some-other-server": {"type": "stdio", "command": "foo"},
            },
            "unrelatedTopLevelKey": "keep-me",
        })

        status = scaffold.project_mcp_wiring(proj)
        check("direct .mcp.json wiring detected",
              status == {"has_wiring": True, "uses_template": False},
              detail=str(status))

        info = api._mcp_wiring_info(bank)
        check("api surfaces has_wiring True, uses_template False",
              info["has_wiring"] is True and info["uses_template"] is False,
              detail=str(info))

        result = api.api_remove_bank(bank.id, drop_index=False, strip_mcp=True)
        check("removal reports mcp_stripped touching .mcp.json",
              result.get("mcp_stripped") == [".mcp.json"], detail=str(result))
        check("bank is gone from the registry", not _bank_exists(bank.id))

        after = json.loads(mcp_json.read_text(encoding="utf-8"))
        check("mnemo key removed",
              scaffold._INSTANCE not in after.get("mcpServers", {}),
              detail=str(after))
        check("foreign mcpServers entry untouched",
              after.get("mcpServers", {}).get("some-other-server")
              == {"type": "stdio", "command": "foo"},
              detail=str(after))
        check("unrelated top-level key untouched",
              after.get("unrelatedTopLevelKey") == "keep-me", detail=str(after))


# --------------------------------------------------------- template layer


def test_template_layer_detected_and_stripped() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo mn13 template ") as tmp:
        proj, memory = _make_project(Path(tmp))
        bank = registry.add(memory, name="mn13-template")

        template = proj / ".mcp.json.template"
        _write_json(template, {
            "mcpServers": {
                scaffold._INSTANCE: scaffold._mcp_server_template(
                    scaffold._VAR_INSTANCE),
                "foreign": {"type": "stdio", "command": "bar"},
            },
        })

        prefix = scaffold._var_prefix(scaffold._VAR_INSTANCE)
        env = proj / ".mcp.env"
        env.write_text(
            f"# foreign var, not ours\n"
            f"OTHER_TOOL_VAR=keep-me\n"
            f"{prefix}_HOST=127.0.0.1\n"
            f"{prefix}_PORT=4646\n"
            f"{prefix}_TOKEN=deadbeefdeadbeefdeadbeefdeadbeef\n",
            encoding="utf-8",
        )

        status = scaffold.project_mcp_wiring(proj)
        check("template-layer wiring detected, uses_template True",
              status == {"has_wiring": True, "uses_template": True},
              detail=str(status))

        info = api._mcp_wiring_info(bank)
        check("api surfaces uses_template True",
              info["has_wiring"] is True and info["uses_template"] is True,
              detail=str(info))

        result = api.api_remove_bank(bank.id, drop_index=False, strip_mcp=True)
        touched = set(result.get("mcp_stripped") or [])
        check("removal reports both template and env touched",
              touched == {".mcp.json.template", ".mcp.env"}, detail=str(result))

        after_template = json.loads(template.read_text(encoding="utf-8"))
        check("mnemo key removed from template",
              scaffold._INSTANCE not in after_template.get("mcpServers", {}),
              detail=str(after_template))
        check("foreign template entry untouched",
              after_template.get("mcpServers", {}).get("foreign")
              == {"type": "stdio", "command": "bar"},
              detail=str(after_template))

        after_env = env.read_text(encoding="utf-8")
        check(f"{prefix}_TOKEN line removed",
              f"{prefix}_TOKEN" not in after_env, detail=after_env)
        check("HOST/PORT left (shared with any other bank's entry)",
              f"{prefix}_HOST=127.0.0.1" in after_env
              and f"{prefix}_PORT=4646" in after_env, detail=after_env)
        check("foreign env line untouched",
              "OTHER_TOOL_VAR=keep-me" in after_env, detail=after_env)


# ---------------------------------------- strip runs after, not before removal


class _LockedQueue:
    """Stands in for the real queue: refuses `drop_bank`, as if the bank
    were still being indexed. Exercises the `index_locked` path without a
    live worker."""

    def drop_bank(self, bank_id: str) -> bool:
        return False

    def resume_bank(self, bank_id: str) -> None:
        pass


def test_index_locked_leaves_wiring_untouched() -> None:
    """MN-13 review fix: a strip that ran BEFORE the index/registry removal
    could leave a project's wiring stripped for a bank that, because removal
    then failed with `index_locked`, still exists. The strip now runs only
    after the bank is provably gone, so a locked-index failure here must
    leave `.mcp.json` byte-for-byte untouched and the bank still registered.
    """
    with tempfile.TemporaryDirectory(prefix="mnemo mn13 locked ") as tmp:
        proj, memory = _make_project(Path(tmp))
        bank = registry.add(memory, name="mn13-locked")
        token = registry.token_for(bank.id)

        mcp_json = proj / ".mcp.json"
        original = {
            "mcpServers": {scaffold._INSTANCE: scaffold._mcp_server(token)},
        }
        _write_json(mcp_json, original)

        real_queue = api._queue
        api._queue = lambda: _LockedQueue()
        try:
            try:
                api.api_remove_bank(bank.id, drop_index=True, strip_mcp=True)
                check("locked removal is rejected", False, detail="did not raise")
            except api.ApiError as exc:
                check("locked removal raises index_locked",
                      exc.code == "index_locked", detail=exc.code)
        finally:
            api._queue = real_queue

        check("bank survives the locked removal", _bank_exists(bank.id))
        after = json.loads(mcp_json.read_text(encoding="utf-8"))
        check(".mcp.json untouched — strip never ran", after == original,
              detail=str(after))


# -------------------------------------------------- strip_mcp omitted/false


def test_strip_mcp_false_is_a_regression_guard() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo mn13 noop ") as tmp:
        proj, memory = _make_project(Path(tmp))
        bank = registry.add(memory, name="mn13-noop")
        token = registry.token_for(bank.id)

        mcp_json = proj / ".mcp.json"
        original = {
            "mcpServers": {scaffold._INSTANCE: scaffold._mcp_server(token)},
        }
        _write_json(mcp_json, original)

        result = api.api_remove_bank(bank.id, drop_index=False)
        check("mcp_stripped key absent when strip_mcp is not requested",
              "mcp_stripped" not in result, detail=str(result))
        check("bank removed as usual", not _bank_exists(bank.id))

        after = json.loads(mcp_json.read_text(encoding="utf-8"))
        check(".mcp.json completely untouched",
              after == original, detail=str(after))


if __name__ == "__main__":
    test_non_project_bank_reports_no_wiring()
    test_project_with_no_mcp_files_reports_no_wiring()
    test_direct_mcp_json_detected_and_stripped()
    test_template_layer_detected_and_stripped()
    test_index_locked_leaves_wiring_untouched()
    test_strip_mcp_false_is_a_regression_guard()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
