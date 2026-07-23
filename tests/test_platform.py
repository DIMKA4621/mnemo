"""Model-independent cross-platform regression checks.

Exercises portable project wiring, project-root resolution, and normalized
index identifiers without touching the user's real mnemo state.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, embedder  # noqa: E402
from src.index import _disk  # noqa: E402
from src.scaffold import (  # noqa: E402
    _HOOK_GROUPS,
    _MCP_SERVER,
    _Refuse,
    _plan_mcp,
    _plan_settings,
)

_passed = _failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"PASS  {name}")
    else:
        _failed += 1
        print(f"FAIL  {name}  {detail}")


def test_scaffold() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo platform ") as raw:
        root = Path(raw) / "проєкт з пробілами"
        root.mkdir()
        mcp_path = root / ".mcp.json"
        settings_path = root / ".claude" / "settings.json"
        settings_path.parent.mkdir()

        mcp_path.write_text(
            json.dumps({"mcpServers": {"foreign": {"command": "other"}}}),
            encoding="utf-8",
        )
        settings_path.write_text(
            json.dumps({"permissions": {"allow": ["Read"]}}),
            encoding="utf-8",
        )

        mcp_text = _plan_mcp(mcp_path, [])
        settings_text = _plan_settings(settings_path, [])
        mcp = json.loads(mcp_text or "{}")
        settings = json.loads(settings_text or "{}")

        check(
            "portable MCP definition",
            mcp.get("mcpServers", {}).get("mnemo") == _MCP_SERVER,
            detail=str(mcp),
        )
        check(
            "foreign MCP server preserved",
            "foreign" in mcp.get("mcpServers", {}),
            detail=str(mcp),
        )
        check(
            "foreign settings preserved",
            settings.get("permissions") == {"allow": ["Read"]},
            detail=str(settings),
        )
        check(
            "all hook groups added",
            all(settings.get("hooks", {}).get(event) for event in _HOOK_GROUPS),
            detail=str(settings.get("hooks")),
        )

        mcp_path.write_text(mcp_text or "", encoding="utf-8")
        settings_path.write_text(settings_text or "", encoding="utf-8")
        check("MCP idempotent", _plan_mcp(mcp_path, []) is None)
        check("hooks idempotent", _plan_settings(settings_path, []) is None)

        legacy = {
            "mcpServers": {
                "mnemo": {
                    "command": "/bin/sh",
                    "args": [
                        "-c",
                        'exec "$HOME/.claude/mnemo/bin/mnemo" mcp',
                    ],
                }
            }
        }
        mcp_path.write_text(json.dumps(legacy), encoding="utf-8")
        refused = False
        try:
            _plan_mcp(mcp_path, [])
        except _Refuse:
            refused = True
        check("legacy MCP definition is an explicit conflict", refused)


def test_project_resolution() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo roots ") as raw:
        base = Path(raw)
        explicit = base / "explicit"
        mnemo_root = base / "env-root"
        claude_root = base / "claude-root"
        for path in (explicit, mnemo_root, claude_root):
            path.mkdir()

        with patch.dict(
            os.environ,
            {
                "MNEMO_ROOT": str(mnemo_root),
                "CLAUDE_PROJECT_DIR": str(claude_root),
            },
            clear=False,
        ):
            check(
                "explicit root wins",
                config.resolve(explicit).root == explicit.resolve(),
            )
            check(
                "MNEMO_ROOT beats CLAUDE_PROJECT_DIR",
                config.resolve(None).root == mnemo_root.resolve(),
            )

        with patch.dict(
            os.environ,
            {"CLAUDE_PROJECT_DIR": str(claude_root)},
            clear=False,
        ):
            os.environ.pop("MNEMO_ROOT", None)
            check(
                "CLAUDE_PROJECT_DIR fallback",
                config.resolve(None).root == claude_root.resolve(),
            )


def test_index_paths() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo paths ") as raw:
        root = Path(raw) / "проєкт"
        memory = root / ".claude" / "memory" / "nested"
        agent = root / ".claude" / "agent-memory" / "reviewer"
        memory.mkdir(parents=True)
        agent.mkdir(parents=True)
        (memory / "topic.md").write_text("# Topic\n", encoding="utf-8")
        (agent / "MEMORY.md").write_text("# Reviewer\n", encoding="utf-8")

        identifiers = sorted(_disk(config.resolve(root)))
        check(
            "stored identifiers use POSIX separators",
            identifiers == [
                ".claude/agent-memory/reviewer/MEMORY.md",
                ".claude/memory/nested/topic.md",
            ],
            detail=str(identifiers),
        )
        check(
            "stored identifiers contain no backslashes",
            all("\\" not in value for value in identifiers),
            detail=str(identifiers),
        )


def test_model_cache_validation() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo cache ") as raw:
        cache = Path(raw)
        required = {
            "config.json",
            "model.onnx",
            "model.onnx_data",
            "tokenizer.json",
            "tokenizer_config.json",
        }
        wrong_snapshot = (
            cache / "models--vendor--different-model" / "snapshots" / "revision"
        )
        near_snapshot = (
            cache
            / "models--user--qdrant--multilingual-e5-large-onnx-copy"
            / "snapshots"
            / "revision"
        )
        right_snapshot = (
            cache
            / "models--qdrant--multilingual-e5-large-onnx"
            / "snapshots"
            / "revision"
        )

        with patch.object(embedder, "MODEL_CACHE", cache):
            check("empty model cache is not warmed", not embedder.is_model_cached())

            wrong_snapshot.mkdir(parents=True)
            for name in required:
                (wrong_snapshot / name).write_bytes(b"complete")
            check(
                "complete different model is not accepted",
                not embedder.is_model_cached(),
            )

            near_snapshot.mkdir(parents=True)
            for name in required:
                (near_snapshot / name).write_bytes(b"complete")
            check(
                "similar repository name is not accepted",
                not embedder.is_model_cached(),
            )

            right_snapshot.mkdir(parents=True)
            (right_snapshot / "model.onnx").write_bytes(b"partial")
            check("partial model cache is not warmed", not embedder.is_model_cached())
            for name in required:
                (right_snapshot / name).write_bytes(b"complete")
            check("complete model cache is warmed", embedder.is_model_cached())


def main() -> int:
    test_scaffold()
    test_project_resolution()
    test_index_paths()
    test_model_cache_validation()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
