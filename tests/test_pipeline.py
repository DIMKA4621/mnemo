"""End-to-end pipeline checks: .md -> chunks -> vectors -> SQLite -> search.

The suite that was missing. `test_platform.py` proves the wiring and
`test_install_windows.py` proves the installer, but nothing exercised the
thing mnemo actually is: a bank of markdown that becomes searchable and
stays in step with the files.

No model, no network, no service. Vectors come from a deterministic
bag-of-words hash defined here, which is enough for every mechanical
property below — chunk boundaries, both retrieval lanes, RRF, the path
filter, incremental reindex, prune, and the rebuild that a provider change
forces. What it deliberately does NOT test is relevance: a hash knows
nothing about meaning. That stays with `test_search.py`, which needs the
real model and cannot run on every push.

The provider lives here rather than beside `local` and `api` on purpose. It
would be one env var away from being switched on in earnest, and a bank
embedded by a hash answers every search with confident nonsense.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import sys
import tempfile
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `config` reads the environment at import time, so the redirect has to happen
# before anything imports it. Without this the test would write its index files
# into the real `~/.claude/mnemo/state` and could collide with a live bank.
_STATE = tempfile.TemporaryDirectory(prefix="mnemo pipeline state ")
os.environ["MNEMO_STATE_DIR"] = _STATE.name

from src import store  # noqa: E402
from src.config import resolve  # noqa: E402
from src.index import _open_bank, reconcile  # noqa: E402
from src.providers.base import EmbeddingProvider  # noqa: E402
from src.search import _fts_ranked, _vector_ranked, search  # noqa: E402

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


# ------------------------------------------------------------- the provider

_DIM = 64


class HashProvider(EmbeddingProvider):
    """Deterministic vectors, no model.

    A bag of words hashed into buckets, then normalised: two texts sharing
    words end up with a high cosine similarity, so kNN returns something
    defensible instead of noise. That is the whole point — the pipeline can
    be exercised end to end without the retrieval lane degenerating into
    "any chunk will do", which would make every ranking assertion vacuous.
    """

    def __init__(self, model: str = "bagofwords-v1") -> None:
        self._model = model

    @property
    def name(self) -> str:
        return "hash"

    @property
    def model(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return _DIM

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        for token in re.findall(r"\w+", text.lower(), re.UNICODE):
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()[:8]
            vec[int(digest, 16) % _DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


# -------------------------------------------------------------- the fixture

_FILES = {
    "MEMORY.md": (
        "# Memory index\n\n"
        "Links and quick facts. Detail lives in topics, day notes in logs.\n"
    ),
    # Deliberately past CHUNK_CAPACITY's ceiling (1200 chars): a file that
    # fits in one chunk would make every boundary assertion below true by
    # having nothing to get wrong.
    "topics/deployment.md": (
        "# Deployment\n\n"
        "How a release reaches production, and how it is taken back out.\n"
        "Every step here is expected to run unattended, so anything that\n"
        "needs a human decision is a defect in the pipeline rather than a\n"
        "step in it.\n\n"
        "## Rollback strategy\n\n"
        "A failed release is reverted by promoting the previous image tag.\n"
        "The rollback never touches the database, because migrations are\n"
        "expand-only and the old code keeps reading the new schema. That\n"
        "constraint is what makes the revert a one-line operation instead\n"
        "of a restore: there is no state to unwind, only a tag to move.\n"
        "The cost is paid earlier, when a column is added rather than\n"
        "renamed, and it is paid deliberately.\n\n"
        "## Health checks\n\n"
        "The load balancer removes an instance after three failed probes.\n"
        "The probe asks the application, not the process table: a process\n"
        "that is running and cannot serve is the case the whole mechanism\n"
        "exists for. Probes are cheap and frequent, and they never touch\n"
        "the database, so a slow query cannot take the fleet out by making\n"
        "every instance look dead at once.\n\n"
        "## Migrations\n\n"
        "A migration ships in its own release, ahead of the code that needs\n"
        "it, and it only ever adds. Dropping a column waits until nothing\n"
        "has read it for a full retention window, which is longer than any\n"
        "rollback we would still consider. The rule is what buys the cheap\n"
        "revert above: schema and code are never required to move together,\n"
        "so either one can be moved back alone.\n"
    ),
    "topics/чанкування.md": (
        "# Чанкування\n\n"
        "Розбиття за заголовками, межі зберігаються як start_char/end_char.\n"
    ),
    "logs/2026-01-02.md": (
        "# 2026-01-02\n\n"
        "Winding back the caching layer; kept notes for the next attempt.\n"
    ),
    "agents/reviewer/MEMORY.md": (
        "# Reviewer\n\n"
        "Reads a diff against the contracts before the lead integrates it.\n"
    ),
}


def _build(root: Path, files: dict[str, str] | None = None) -> None:
    for rel, text in (files or _FILES).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _paths_of(hits) -> list[str]:
    return [h.path for h in hits]


def _chunks_of(conn, path: str) -> list:
    return conn.execute(
        "SELECT chunk_index, heading, content, start_char, end_char "
        "FROM chunks WHERE path = ? ORDER BY chunk_index",
        (path,),
    ).fetchall()


# ----------------------------------------------------------------- the tests


def test_index_and_chunks() -> None:
    """A bank becomes an index, and the chunks still describe the file."""
    print("\n=== index and chunk boundaries ===")
    with tempfile.TemporaryDirectory(prefix="mnemo bank ") as raw:
        root = Path(raw) / "проєкт" / ".claude" / "memory"
        _build(root)
        provider = HashProvider()
        conn = _open_bank(resolve(root), provider, False)
        try:
            result = reconcile(conn, provider, resolve(root).root)
            check("every markdown file is indexed",
                  result.files_indexed == len(_FILES),
                  detail=f"{result.files_indexed} of {len(_FILES)}")
            check("no file failed", not result.errors, detail=str(result.errors))
            check("a multi-section file yields several chunks",
                  result.chunks_indexed > len(_FILES),
                  detail=f"{result.chunks_indexed} chunks")
            check("the store agrees with the reconcile",
                  store.file_count(conn) == len(_FILES)
                  and store.chunk_count(conn) == result.chunks_indexed,
                  detail=f"{store.file_count(conn)} files, "
                         f"{store.chunk_count(conn)} chunks")

            # The chunk viz in the cabinet draws these offsets over the raw
            # file, so a chunk whose text is not exactly the slice it claims
            # would draw a boundary in the wrong place.
            source = (root / "topics/deployment.md").read_text(encoding="utf-8")
            rows = _chunks_of(conn, "topics/deployment.md")
            check("chunk indices are dense and ordered",
                  [r["chunk_index"] for r in rows] == list(range(len(rows))),
                  detail=str([r["chunk_index"] for r in rows]))
            check("every chunk is exactly the slice it points at",
                  all(source[r["start_char"]:r["end_char"]] == r["content"]
                      for r in rows),
                  detail=f"{len(rows)} chunks")
            check("headings are carried, not invented",
                  {r["heading"] for r in rows} <= {
                      "Deployment", "Rollback strategy", "Health checks",
                      "Migrations"},
                  detail=str(sorted({r["heading"] for r in rows})))

            # Paths are identifiers, not filesystem paths: they must read the
            # same on Windows as they do everywhere else.
            stored = {r["path"] for r in store.list_files(conn)}
            check("stored paths are POSIX-relative",
                  stored == set(_FILES), detail=str(sorted(stored)))
        finally:
            conn.close()


def test_both_retrieval_lanes() -> None:
    """Vector, lexical, and the fusion of the two — each proved separately.

    Asserting only through `search()` would let one dead lane hide behind
    the other: RRF returns a plausible answer as long as *something* ranks.
    """
    print("\n=== retrieval lanes ===")
    with tempfile.TemporaryDirectory(prefix="mnemo bank ") as raw:
        root = Path(raw) / "memory"
        _build(root)
        provider = HashProvider()
        conn = _open_bank(resolve(root), provider, False)
        try:
            reconcile(conn, provider, resolve(root).root)

            def path_of(rowid: int) -> str:
                row = conn.execute(
                    "SELECT path FROM chunks WHERE id = ?", (rowid,)
                ).fetchone()
                return row["path"]

            vec_ids = _vector_ranked(conn, provider.embed_query(
                "rollback strategy"), 10)
            check("the vector lane ranks something", bool(vec_ids))
            check("and ranks the right file first",
                  bool(vec_ids) and path_of(vec_ids[0]) == "topics/deployment.md",
                  detail=path_of(vec_ids[0]) if vec_ids else "nothing")

            fts_ids = _fts_ranked(conn, "rollback", 10, None)
            check("the lexical lane finds an exact term", bool(fts_ids))
            check("and it is in the same file",
                  bool(fts_ids) and path_of(fts_ids[0]) == "topics/deployment.md",
                  detail=path_of(fts_ids[0]) if fts_ids else "nothing")

            hits = search(conn, "rollback strategy", provider=provider, top_k=3)
            check("the fused search answers",
                  bool(hits) and hits[0].path == "topics/deployment.md",
                  detail=str(_paths_of(hits)))
            check("a hit carries its heading and its text",
                  bool(hits) and hits[0].heading and "rollback" in
                  hits[0].content.lower(),
                  detail=hits[0].heading if hits else "")

            # Non-ASCII content has to survive the whole round trip: the file
            # name, the FTS tokeniser and the chunk text.
            ukr = search(conn, "чанкування", provider=provider, top_k=3)
            check("a Ukrainian query finds its Ukrainian file",
                  "topics/чанкування.md" in _paths_of(ukr),
                  detail=str(_paths_of(ukr)))
        finally:
            conn.close()


def test_path_prefix_narrows() -> None:
    """`path_prefix` is navigation: it must narrow, and never leak."""
    print("\n=== path prefix ===")
    with tempfile.TemporaryDirectory(prefix="mnemo bank ") as raw:
        root = Path(raw) / "memory"
        _build(root)
        provider = HashProvider()
        conn = _open_bank(resolve(root), provider, False)
        try:
            reconcile(conn, provider, resolve(root).root)

            scoped = search(conn, "memory notes", provider=provider,
                            top_k=10, path_prefix="agents/reviewer")
            check("a prefix returns only what is under it",
                  scoped and all(p.startswith("agents/reviewer/")
                                 for p in _paths_of(scoped)),
                  detail=str(_paths_of(scoped)))

            wide = search(conn, "memory notes", provider=provider, top_k=10)
            check("and the same query unscoped reaches further",
                  len(_paths_of(wide)) > len(_paths_of(scoped)),
                  detail=f"{len(wide)} vs {len(scoped)}")

            # A folder that shares a prefix with a real one must not match:
            # `topics` and `topicsx` are different places.
            check("a prefix that matches no folder returns nothing",
                  search(conn, "rollback", provider=provider,
                         path_prefix="topicsx") == [],
                  detail="topicsx leaked")
        finally:
            conn.close()


def test_incremental_reindex() -> None:
    """The second run must do nothing, and an edit must replace, not add."""
    print("\n=== incremental reindex ===")
    with tempfile.TemporaryDirectory(prefix="mnemo bank ") as raw:
        root = Path(raw) / "memory"
        _build(root)
        provider = HashProvider()
        paths = resolve(root)
        conn = _open_bank(paths, provider, False)
        try:
            first = reconcile(conn, provider, paths.root)

            again = reconcile(conn, provider, paths.root)
            check("a no-change run indexes nothing",
                  again.files_indexed == 0 and again.chunks_indexed == 0,
                  detail=f"{again.files_indexed} files, "
                         f"{again.chunks_indexed} chunks")
            check("and prunes nothing", again.files_pruned == 0)
            check("the index is unchanged",
                  store.chunk_count(conn) == first.chunks_indexed)

            # An edit must REPLACE the file's chunks. Appending them would
            # leave the removed text searchable forever, which is the failure
            # mode a memory system can least afford.
            (root / "topics/deployment.md").write_text(
                "# Deployment\n\n"
                "## Blue-green cutover\n\n"
                "Traffic is switched by flipping the router weight.\n",
                encoding="utf-8",
            )
            edited = reconcile(conn, provider, paths.root)
            check("only the edited file is reindexed",
                  edited.files_indexed == 1, detail=str(edited.files_indexed))
            check("the new text is searchable",
                  "topics/deployment.md" in _paths_of(
                      search(conn, "blue-green cutover", provider=provider)))
            # Stated against the index, not against the result list. A manual
            # search is ungated on purpose -- it returns the k nearest and
            # lets the agent judge -- so in a small bank *some* chunk comes
            # back for any query. "The path is absent from the results" would
            # therefore be a claim about bank size; "the text is absent from
            # the index" is the property that actually matters.
            check("the removed text is gone from the index",
                  not _fts_ranked(conn, "rollback", 10, None)
                  and not any("rollback" in r["content"].lower()
                              for r in _chunks_of(conn, "topics/deployment.md")),
                  detail="a deleted section is still indexed")
            check("no orphan chunks were left behind",
                  all(r["heading"] in ("Deployment", "Blue-green cutover")
                      for r in _chunks_of(conn, "topics/deployment.md")),
                  detail=str([r["heading"] for r in
                              _chunks_of(conn, "topics/deployment.md")]))
        finally:
            conn.close()


def test_prune_follows_the_files() -> None:
    """Deleted and renamed files must leave the index — FR-8, invariant."""
    print("\n=== prune ===")
    with tempfile.TemporaryDirectory(prefix="mnemo bank ") as raw:
        root = Path(raw) / "memory"
        _build(root)
        provider = HashProvider()
        paths = resolve(root)
        conn = _open_bank(paths, provider, False)
        try:
            reconcile(conn, provider, paths.root)

            (root / "logs/2026-01-02.md").unlink()
            after = reconcile(conn, provider, paths.root)
            check("a deleted file is pruned", after.files_pruned == 1,
                  detail=str(after.files_pruned))
            check("its chunks go with it",
                  not _chunks_of(conn, "logs/2026-01-02.md"))
            check("and it can no longer be found",
                  "logs/2026-01-02.md" not in _paths_of(
                      search(conn, "caching layer", provider=provider)))

            # A rename is a delete plus an add, and the old identifier must
            # not survive it: nothing on disk would ever contradict it again.
            (root / "topics/deployment.md").rename(root / "topics/release.md")
            renamed = reconcile(conn, provider, paths.root)
            check("a rename prunes the old path and indexes the new one",
                  renamed.files_pruned == 1 and renamed.files_indexed == 1,
                  detail=f"pruned={renamed.files_pruned} "
                         f"indexed={renamed.files_indexed}")
            stored = {r["path"] for r in store.list_files(conn)}
            check("only the new path remains",
                  "topics/release.md" in stored
                  and "topics/deployment.md" not in stored,
                  detail=str(sorted(stored)))
        finally:
            conn.close()


def test_provider_change_rebuilds() -> None:
    """Vectors from two providers are not comparable, so the bank rebuilds."""
    print("\n=== provider change ===")
    with tempfile.TemporaryDirectory(prefix="mnemo bank ") as raw:
        root = Path(raw) / "memory"
        _build(root)
        paths = resolve(root)

        first = HashProvider()
        conn = _open_bank(paths, first, False)
        try:
            built = reconcile(conn, first, paths.root)
            check("the bank records which provider filled it",
                  store.get_meta(conn).get("provider_key") == first.key,
                  detail=str(store.get_meta(conn).get("provider_key")))
        finally:
            conn.close()

        second = HashProvider(model="bagofwords-v2")
        conn = _open_bank(paths, second, False)
        try:
            check("a different provider empties the index on open",
                  store.chunk_count(conn) == 0,
                  detail=f"{store.chunk_count(conn)} chunks survived")
            check("and the fingerprint is the new one",
                  store.get_meta(conn).get("provider_key") == second.key,
                  detail=str(store.get_meta(conn).get("provider_key")))
            rebuilt = reconcile(conn, second, paths.root)
            check("the rebuild restores the same shape from the .md alone",
                  rebuilt.chunks_indexed == built.chunks_indexed,
                  detail=f"{rebuilt.chunks_indexed} vs {built.chunks_indexed}")
        finally:
            conn.close()

        # Same provider again: nothing to rebuild, and nothing lost.
        conn = _open_bank(paths, second, False)
        try:
            check("reopening with the same provider keeps the index",
                  store.chunk_count(conn) == built.chunks_indexed,
                  detail=f"{store.chunk_count(conn)} chunks")
        finally:
            conn.close()


def test_missing_extension_support_is_explained() -> None:
    """A Python that cannot load extensions must say so, not AttributeError.

    This is not hypothetical: the macOS build actions/setup-python installs
    is exactly such a Python, and `import sqlite_vec` succeeds on it, so the
    installer's import probe reported a clean install and the first search
    was what finally failed -- eight frames deep, as
    `'sqlite3.Connection' object has no attribute 'enable_load_extension'`.
    """
    print("\n=== extension support is reported, not discovered ===")

    class NoExtensions:
        """An interpreter's connection without the attribute, exactly."""

    try:
        store.load_vec(NoExtensions())
    except store.VectorExtensionUnavailable as exc:
        message = str(exc)
    except Exception as exc:  # noqa: BLE001 - any other type is the failure
        message = ""
        check("the missing capability raises our own error", False,
              detail=f"{type(exc).__name__}: {exc}")
    else:
        message = ""
        check("the missing capability raises at all", False)

    if message:
        check("the missing capability raises our own error", True)
        check("the message says what is wrong",
              "loadable SQLite extensions" in message, detail=message)
        check("and what to do about it",
              "Homebrew" in message and "installer" in message,
              detail=message)

    # The machine running this must, of course, be able to load it.
    check("this interpreter can load sqlite-vec",
          store.vector_support() is None, detail=str(store.vector_support()))


def main() -> int:
    test_index_and_chunks()
    test_both_retrieval_lanes()
    test_path_prefix_narrows()
    test_incremental_reindex()
    test_prune_follows_the_files()
    test_provider_change_rebuilds()
    test_missing_extension_support_is_explained()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
