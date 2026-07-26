# mnemo

Shared, searchable **project memory** for Claude Code and its agents.

```
.md  →  chunks  →  embeddings  →  sqlite-vec (+ FTS5)  →  search
```

Curated markdown is the **single source of truth**. A local, disposable,
rebuildable vector index makes it searchable — nothing in your repo but
plain `.md` and a little wiring.

> **v3 is being built on `feat/v3`.** Memory becomes a set of **banks**
> (any root folder of `.md`, anywhere on disk) served by a persistent
> local service that watches the files itself, with MCP over HTTP and a
> local web cabinet. This README describes **what works today**; what has
> not landed yet is called out where it matters.

## Quick start

Open the project in Claude Code and tell it:

> adopt mnemo into this project

The `mnemo-adopt` skill takes it from there — installing the engine,
wiring the project, scaffolding the memory rule — showing a diff and
asking before anything non-trivial. It never commits for you.

That's the whole onboarding. From then on the project's memory is kept
indexed and the relevant bits surface on their own.

## How it works

Two layers, cleanly separated:

| Layer | Where | In git? |
|---|---|---|
| Engine (code, venv, model, index) | `~/.claude/mnemo/` — installed once, shared by every project | no |
| Source of truth (`.md`) | a **bank**: one root folder of `.md`, anywhere — for a project, typically `<project>/.claude/memory/` | **yes**, when the folder is inside a repo |
| Index DB | `~/.claude/mnemo/state/<bankhash>.db` — one per bank root, rebuildable | no |

A bank is **flat**: the whole folder tree under its root is one index,
with no internal scopes. Everything `*.md` below the root is indexed
(minus `.git`, `.venv`, `node_modules`, `__pycache__`), so point a bank
at your memory folder rather than at a whole repository unless you really
do want every markdown file in it. Need memory kept apart — per agent,
say? Use a **separate bank**, not a scope. `--path-prefix` narrows a
search to a subfolder, but that is navigation, not isolation.

The engine installs on Linux, macOS and native Windows — `install.sh` on
POSIX, `install.ps1` (built-in PowerShell 5.1+, 64-bit Python 3.10+, no
WSL/PATH needed) on Windows. On Windows the installer sets the user
`HOME` if absent and refuses a value that differs from `%USERPROFILE%`,
so MCP and hooks resolve the same canonical path. After first creating
`HOME`, close and reopen the launching terminal or IDE before restarting
Claude Code. Everything below is identical across platforms.

Adoption commits a tiny bit of git-tracked wiring into the project so it
travels with the repo:

- **`.mcp.json`** — registers the `mnemo` MCP server. Claude Code spawns
  `mnemo mcp` over stdio per session (not a daemon), scoped to this
  project. Tools: `memory_search(query, path_prefix, top_k)`,
  `memory_reindex`.
- **`.claude/settings.json`** — three hooks:
  - `SessionStart` → `mnemo ingest` — full reconcile (catches outside
    changes like a `git pull`).
  - `PostToolUse` (Edit/Write/MultiEdit) → `mnemo hook-postedit` —
    reindexes when the edited file is an `.md` inside the bank; any
    other edit is an instant no-op.
  - `UserPromptSubmit` → `mnemo hook-inject` — embeds your prompt,
    searches this project's memory, and surfaces the relevant sections
    into the turn.
- **`.claude/rules/mnemo-memory.md`** — the binding memory rule, loaded
  for the main session and every subagent.

> **This wiring changes at v3 phase 4.** MCP moves to HTTP against the
> running service (no per-session spawn), the reindexing hooks disappear
> because the watcher does that job, and only the auto-inject hook stays.
> Nothing is asked of you yet — but do not hand-build wiring around the
> current shape today.

Embedding is served by one warm helper per machine (`embed-server`,
loopback only) so hooks stay light and CPU stays bounded. It starts on
first need and then stays resident — idle exit is off by default, because
exiting after half an hour cost about 9 seconds on the next search. It
holds **one** copy of the model: ~1.6 GB steady state, with a transient
peak that depends on batch size and the longest chunk in a batch (~2.1 GB
on Ukrainian markdown at the default batch of 16). The model is **never**
downloaded implicitly — `warmup` is the only step that fetches it.

If the helper dies below Python — a segfault inside the ONNX runtime — it
leaves a native stack dump at `~/.claude/mnemo/embed-crash.log`, and the
client says so instead of falling back in silence. Both cases end in an
in-process model (a second ~2.2 GB, far slower), but only one of them
means something broke, so they read differently. An empty crash log after
the helper disappears means something killed it; a dump means it fell over.

The index is disposable: delete a bank's `state/*.db`, run `mnemo
ingest`, and you are back to an identical state. The `.md` is the only
thing that matters.

## Commands

```bash
mnemo warmup                 # one-time model download + sanity check
mnemo init [--root DIR]      # additive, idempotent project wiring
mnemo ingest [--root DIR]    # reconcile .md -> index (hash-diff + prune)
mnemo search "query" [--path-prefix SUBFOLDER] [-k N]
mnemo mcp                    # stdio MCP server (agent tools)
```

`hook-postedit`, `hook-inject` and `embed-server` exist too but are
invoked by the hooks, not by hand. `$MNEMO_ROOT` overrides the bank
root; `--root` defaults to the current directory.

The v2 `--scope project|agent` / `--agent NAME` flags are **gone**: banks
are flat, and `--path-prefix` replaces them with any folder depth. The
first v3 run also rebuilds the index — the schema changed, and rebuilding
from the `.md` is the migration. Older index files keyed by a project
root are left alone: never opened, never deleted for you.

Service control, banks management and the web cabinet arrive with the
later v3 phases; the target command set is listed in
`docs/Memory-contracts-v3.md` §11.1.

## Run in a container

Dev/worker containers reuse the **host engine read-only** and keep their own
**ephemeral** index: mount the engine (code + venv + model), point
`$MNEMO_STATE_DIR` at an in-container path, and mount the project's `.md`. The
index lives inside the container and dies with it — no host garbage, no
re-download, and the `.md` in git stays the only source of truth.

```yaml
# docker-compose.yml
services:
  worker:
    image: your-image                 # needs python3.12 + libgomp1
    volumes:
      - ${HOME}/.claude/mnemo:/root/.claude/mnemo:ro   # engine + venv + model (read-only)
      - ./project:/workspace/proj:rw                     # the .md memory
    environment:
      MNEMO_STATE_DIR: /tmp/mnemo   # ephemeral index — dies with the container
      MNEMO_ROOT: /workspace/proj
    # tmpfs:                        # optional: keep the index in RAM
    #   - /tmp/mnemo
```

```bash
docker compose run --rm worker \
  /root/.claude/mnemo/bin/mnemo search "query" --root /workspace/proj
```

This recipe drives the engine directly, which is how the CLI still works
today; the index file is now keyed by the **bank root** you pass as
`--root` (or `MNEMO_ROOT`), so a container and the host share an index
only when that root resolves to the same path in both.

`MNEMO_STATE_DIR` is the whole trick: it relocates only the writable state
(index + logs + token), so the engine and model-cache stay read-only and
shared. Prerequisite: the host is warmed once (`mnemo warmup`); the base image
needs the host venv's Python minor (`cp312`) at `/usr/bin/python3` plus
`libgomp1`. Full recipe + an example compose file: [`docs/containers/`](docs/containers/README.md).

## Develop

This repo **is** the system. `install.sh` (POSIX) / `install.ps1`
(Windows) mirrors `src/` into the engine home; tests run against the
source:

```bash
# Linux / macOS
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python tests/test_search.py   # labeled recall eval
.venv/bin/python tests/test_mcp.py      # standalone MCP client check
```
```powershell
# native Windows (PowerShell 5.1+)
py -3 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python tests\test_search.py   # labeled recall eval
.\.venv\Scripts\python tests\test_mcp.py      # standalone MCP client check
```

Design source of truth (Ukrainian): `docs/Memory-design-v3.md` (what and
why), `docs/Memory-requirements-v3.md` (FR/NFR),
`docs/Memory-implementation-v3.md` (stack, blocks, phases) and
`docs/Memory-contracts-v3.md` (module ownership + exact API shapes).
`docs/Setup-design.md` covers the install model; `docs/Memory-design-v2.md`
and `-v1.md` are historical. Engine: `multilingual-e5-large` via
`fastembed` (ONNX, no torch); vector search primary, FTS5/BM25 secondary,
blended with RRF.
