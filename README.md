# memory-poc

Proof-of-concept for the **shared project memory** (Memory-design-v2). This step
validates ONLY the core mechanism:

```
.md  →  chunks  →  embeddings  →  our sqlite-vec  →  search
```

Hooks / MCP server / auto-injection are deliberately **out of scope here** —
but the on-disk layout under `.claude/` is the *real* project layout, so the
pipeline runs against the production-shaped structure from day one.

## Layout

```
.claude/
  memory/                    general project memory (single source of truth)
    MEMORY.md                thin index
    logs/2026-05-16.md       day log
    architecture.md          topic file
    deployment-notes.md      topic file
  agent-memory/              per-agent scoped memory
    developer/MEMORY.md
    tester/MEMORY.md
src/
  config.py                  one place for model / paths / knobs
  chunker.py                 semantic-text-splitter wrapper (heading-aware)
  embedder.py                fastembed + multilingual-e5-base (query:/passage:)
  store.py                   sqlite-vec + FTS5 + change-state (hashes in the DB)
  index.py                   walk + sha256-diff + reindex changed + prune
  search.py                  vector kNN + FTS5 + RRF + scope filter
  cli.py                     `ingest` / `search`
memory.db                    sqlite-vec — generated, gitignored, rebuildable
```

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m src.cli ingest                       # .md -> vector index
python -m src.cli search "як деплоїмо на прод"  # semantic search (UA)
python -m src.cli search "rollback strategy" --scope project
python -m src.cli search "хто пише тести" --scope agent --agent tester
```

## Decisions

- **Model:** `intfloat/multilingual-e5-large` (1024-dim) via `fastembed`
  (ONNX, no torch). The decision was e5-base, but fastembed 0.8.0 ships no
  e5-base — e5-large is the same family (best multilingual here), so it is
  the documented fallback. The e5 `query:` / `passage:` prefix convention is
  hidden inside `embedder.py`. Swapping back to e5-base later is one line.
- **First-run note:** `embedder.py` validates the model against the installed
  fastembed build and prints available multilingual models on mismatch.
- **Index is disposable:** delete `memory.db`, run `ingest`, identical state.
  The `.md` files are the only source of truth.
- Vector search is primary; FTS5/BM25 is the secondary lexical net; blended
  with reciprocal rank fusion.
