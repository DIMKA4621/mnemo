"""MN-7: empty directories must not appear in `api_tree`'s output.

No model, no network, no live service — `api_tree` is a plain function once
imported, and a bank never gets indexed here, so `_bank_conn` short-circuits
to `None` (§`api.py`) without needing sqlite-vec or a provider.

    .venv/bin/python tests/test_tree_prune.py
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
# `mkdtemp`, not `TemporaryDirectory`: MN-25's `test_api_add_bank_create_
# structure` calls `api.api_add_bank`, which opens `service.db` through
# `servicelog` — that module caches a per-thread READER connection
# `servicelog.close()` does not close, so a `TemporaryDirectory`'s exit-time
# auto-cleanup races that still-open handle and Windows refuses the unlink.
# Same convention as test_watcher.py / test_service_ctl.py /
# test_service_recovery.py.
_STATE = tempfile.mkdtemp(prefix="mnemo tree-prune state ")
os.environ["MNEMO_STATE_DIR"] = _STATE

from src import api, registry  # noqa: E402

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


def _dir_names(node: dict) -> set[str]:
    return {c["name"] for c in node["children"] if c["type"] == "dir"}


def _find(node: dict, name: str) -> dict | None:
    for c in node["children"]:
        if c["type"] == "dir" and c["name"] == name:
            return c
    return None


# ------------------------------------------------------------- pure helpers


def test_prune_pure() -> None:
    leaf = {"type": "file", "name": "a.md", "path": "keep/a.md"}
    empty = {"type": "dir", "name": "empty", "path": "empty", "children": []}
    only_files_excluded = {
        "type": "dir", "name": "drafts", "path": "drafts", "children": [],
    }
    kept = {"type": "dir", "name": "keep", "path": "keep", "children": [leaf]}
    deep_leaf = {
        "type": "file", "name": "deep.md", "path": "nested/level1/level2/deep.md",
    }
    level2 = {
        "type": "dir", "name": "level2", "path": "nested/level1/level2",
        "children": [deep_leaf],
    }
    level1 = {
        "type": "dir", "name": "level1", "path": "nested/level1",
        "children": [level2],
    }
    nested = {
        "type": "dir", "name": "nested", "path": "nested", "children": [level1],
    }
    depth_cut = {
        "type": "dir", "name": "cutoff", "path": "cutoff", "children": [],
    }
    root = {
        "type": "dir",
        "name": "",
        "path": "",
        "children": [empty, only_files_excluded, kept, nested, depth_cut],
    }

    api._prune_empty_dirs(root, {"cutoff"})

    check("empty dir pruned", "empty" not in _dir_names(root))
    check("dir with no .md children pruned",
          "drafts" not in _dir_names(root))
    check("dir with a file survives", "keep" in _dir_names(root))
    check("ancestor chain to a deep file survives",
          "nested" in _dir_names(root)
          and _find(_find(root, "nested"), "level1") is not None
          and _find(_find(_find(root, "nested"), "level1"), "level2")
          is not None)
    check("a dir truncated by depth survives with no children",
          "cutoff" in _dir_names(root))
    # kept: "keep", "nested", "nested/level1", "nested/level1/level2", "cutoff"
    check("dirs count matches what's shown", api._count_dirs(root) == 5,
          detail=f"got {api._count_dirs(root)}")


# --------------------------------------------------------- through api_tree


def test_api_tree() -> None:
    with tempfile.TemporaryDirectory(prefix="mnemo tree-prune bank ") as tmp:
        root = Path(tmp)
        (root / "empty_dir").mkdir()
        (root / "only_txt").mkdir()
        (root / "only_txt" / "note.txt").write_text("not markdown")
        (root / "keep").mkdir()
        (root / "keep" / "a.md").write_text("# A")
        (root / "nested" / "level1" / "level2").mkdir(parents=True)
        (root / "nested" / "level1" / "level2" / "deep.md").write_text("# Deep")
        (root / "drafts").mkdir()
        (root / "drafts" / "draft.md").write_text("# Draft")

        bank = registry.add(root, name="tree-prune-test")
        registry.update(bank.id, exclude=["drafts/*.md"])

        result = api.api_tree(bank.name)

        check("files count excludes the excluded .md", result["files"] == 2,
              detail=f"got {result['files']}")
        check("dirs count matches the shown tree",
              result["dirs"] == api._count_dirs(result["tree"]),
              detail=f"dirs={result['dirs']} "
                     f"counted={api._count_dirs(result['tree'])}")
        check("dirs count is exactly the 4 real ancestors", result["dirs"] == 4,
              detail=f"got {result['dirs']}")

        top = _dir_names(result["tree"])
        check("empty_dir is not in the tree", "empty_dir" not in top,
              detail=str(top))
        check("only_txt (no .md inside) is not in the tree",
              "only_txt" not in top, detail=str(top))
        check("drafts (only an excluded .md inside) is not in the tree",
              "drafts" not in top, detail=str(top))
        check("keep is in the tree", "keep" in top, detail=str(top))
        check("nested is in the tree", "nested" in top, detail=str(top))

        level1 = _find(_find(result["tree"], "nested"), "level1")
        check("nested/level1 survives as an ancestor", level1 is not None)
        level2 = _find(level1, "level2") if level1 else None
        check("nested/level1/level2 survives as an ancestor", level2 is not None)
        check("deep.md is reachable at the bottom of the chain",
              level2 is not None
              and any(c["name"] == "deep.md" for c in level2["children"]))


# ---------------------------------------------- depth truncation vs. pruning


def test_depth_truncation_not_mistaken_for_empty() -> None:
    """A branch `os.walk` never reached because of `depth` is not "empty" —
    it just wasn't looked at. Reviewer-reported regression: pruning treated
    the two the same and deleted a branch that has a real `.md` in it, just
    deeper than the requested `depth`."""
    with tempfile.TemporaryDirectory(prefix="mnemo tree-prune depth ") as tmp:
        root = Path(tmp)
        (root / "a" / "b" / "c" / "d").mkdir(parents=True)
        (root / "a" / "b" / "c" / "d" / "real.md").write_text("# Real")

        bank = registry.add(root, name="tree-prune-depth-test")

        unlimited = api.api_tree(bank.name, depth=0)
        check("depth=0 sees the file 4 levels down", unlimited["files"] == 1,
              detail=f"got {unlimited['files']}")
        check("depth=0 counts all 4 ancestor dirs", unlimited["dirs"] == 4,
              detail=f"got {unlimited['dirs']}")

        limited = api.api_tree(bank.name, depth=3)
        check("depth=3 does not wipe the whole tree",
              limited["dirs"] > 0 and len(limited["tree"]["children"]) > 0,
              detail=str(limited))

        node = limited["tree"]
        for name in ("a", "b", "c"):
            node = _find(node, name)
            check(f"{name!r} survives a depth cutoff instead of being pruned",
                  node is not None, detail=str(limited["tree"]))
            if node is None:
                return
        check("the depth-cut node itself carries no children "
              "(not explored, but still shown)",
              node["children"] == [])


# -------------------------------------------- api_fs_dirs has_claude_memory


def test_api_fs_dirs_has_claude_memory() -> None:
    """MN-25: the add-bank dialog's "create structure" checkbox gates on
    this field — a bare folder must report `False`, one that already holds
    `.claude/memory` must report `True`."""
    with tempfile.TemporaryDirectory(prefix="mnemo fs-dirs bank ") as tmp:
        root = Path(tmp)

        bare = api.api_fs_dirs(str(root))
        check("bare folder reports has_claude_memory=False",
              bare["has_claude_memory"] is False, detail=str(bare))

        (root / ".claude" / "memory").mkdir(parents=True)
        seeded = api.api_fs_dirs(str(root))
        check("folder with .claude/memory reports has_claude_memory=True",
              seeded["has_claude_memory"] is True, detail=str(seeded))


def test_memory_dir_for_three_shapes() -> None:
    """MN-25 review: `_memory_dir_for`'s three cases, through `api_fs_dirs`'s
    `memory_dir` field — an ordinary folder, a bare `.claude` picked directly
    (the doubling bug the user caught live), and an already-`.claude/memory`
    path."""
    with tempfile.TemporaryDirectory(prefix="mnemo memory-dir bank ") as tmp:
        root = Path(tmp)
        (root / ".claude" / "memory").mkdir(parents=True)

        ordinary = api.api_fs_dirs(str(root))
        check("ordinary folder: memory_dir appends .claude/memory",
              ordinary["memory_dir"] == (root / ".claude" / "memory").as_posix(),
              detail=str(ordinary))

        bare_claude = api.api_fs_dirs(str(root / ".claude"))
        check("bare .claude: memory_dir appends only memory (no doubling)",
              bare_claude["memory_dir"] == (root / ".claude" / "memory").as_posix(),
              detail=str(bare_claude))
        check("bare .claude: has_claude_memory true (memory/ already exists)",
              bare_claude["has_claude_memory"] is True, detail=str(bare_claude))

        already = api.api_fs_dirs(str(root / ".claude" / "memory"))
        check("already .claude/memory: memory_dir equals the path itself",
              already["memory_dir"] == (root / ".claude" / "memory").as_posix(),
              detail=str(already))
        check("already .claude/memory: has_claude_memory false (irrelevant here)",
              already["has_claude_memory"] is False, detail=str(already))


def test_api_add_bank_create_structure() -> None:
    """MN-25 review: exercise `api_add_bank`'s `create_structure` branch
    directly — happy path, and the two rejection cases (not a
    `.claude/memory`-shaped path, not absolute)."""
    with tempfile.TemporaryDirectory(prefix="mnemo add-bank bank ") as tmp:
        root = Path(tmp)
        memory_dir = root / ".claude" / "memory"

        req = api.AddBankRequest(
            root=str(memory_dir), name="mn25-create-structure",
            create_structure=True,
        )
        info = api.api_add_bank(req)
        check("bank got registered", info.get("name") == "mn25-create-structure",
              detail=str(info))
        check(".claude/memory got created", memory_dir.is_dir())
        check(".claude/rules got created", (root / ".claude" / "rules").is_dir())
        check("MEMORY.md got created", (memory_dir / "MEMORY.md").is_file())

        bad_shape = api.AddBankRequest(
            root=str(root / "not-memory"), create_structure=True,
        )
        try:
            api.api_add_bank(bad_shape)
            check("non-.claude/memory shape is rejected", False,
                  detail="did not raise")
        except api.ApiError as exc:
            check("non-.claude/memory shape is rejected",
                  exc.code == "bad_request", detail=exc.code)

        relative = api.AddBankRequest(
            root=".claude/memory", create_structure=True,
        )
        try:
            api.api_add_bank(relative)
            check("relative root is rejected", False, detail="did not raise")
        except api.ApiError as exc:
            check("relative root is rejected", exc.code == "bad_request",
                  detail=exc.code)


if __name__ == "__main__":
    test_prune_pure()
    test_api_tree()
    test_depth_truncation_not_mistaken_for_empty()
    test_api_fs_dirs_has_claude_memory()
    test_memory_dir_for_three_shapes()
    test_api_add_bank_create_structure()
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
