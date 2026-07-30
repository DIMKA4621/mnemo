# CLAUDE.md — mnemo

You are developing **mnemo**: a project-memory system for Claude Code and
agents. This repository IS the system itself (not a project that merely
uses it).

## What mnemo is

Curated markdown is the **single source of truth**; a local, disposable,
rebuildable vector index makes it searchable.

```
.md  →  chunks  →  embeddings  →  sqlite-vec (+ FTS5)  →  search
```

- Source of truth: **banks** — arbitrary root folders anywhere on disk,
  each with freely nested `.md`. A bank is **flat**: no internal scopes,
  the whole folder is one index. Need isolation (e.g. per agent) → a
  separate bank + its own MCP connection. `path_prefix` narrows a search
  to a subfolder, but it is navigation, not an access boundary.
- Index: one SQLite file per bank at `~/.claude/mnemo/state/<bankhash>.db`
  — gitignored realm, deletable, fully rebuildable from the `.md`.
- Access: **one persistent local service** owns the registry, the index
  and the watcher; CLI, MCP, hooks and the web cabinet are thin clients
  of its loopback HTTP API. v3 deliberately reverses v2's "no server, no
  daemon" — the daemon is the point.

## Design source of truth

The v3 set, all four authoritative and mutually consistent:

- `docs/Memory-design-v3.md` — *what and why*; section 13 is the
  decision list. **Read it before changing architecture.**
- `docs/Memory-requirements-v3.md` — *what must hold* (FR/NFR =
  acceptance criteria).
- `docs/Memory-implementation-v3.md` — *how and in what order* (stack,
  blocks A–L, phases 0–7, each with a `✅ Перевірка`).
- `docs/Memory-contracts-v3.md` — module ownership plus the exact HTTP /
  MCP / CLI / registry / store shapes.

`.claude/rules/v3-build.md` carries the binding build rules — it loads
for every subagent (they do not inherit this file). `docs/Memory-design-v2.md`
and `-v1.md` are historical only; `docs/Setup-design.md` covers the
install model and is mid-transition (see its header).

Project decision history, research and rationale live in **this repo's own
bank**: `.claude/memory/` (`MEMORY.md` index, `logs/`, `topics/`), served by
the `mnemo` MCP server in `.mcp.json`. **Search it with the `search` tool
before planning** — do not re-investigate what is already recorded.

It used to sit in Claude Code's per-project store
(`~/.claude/projects/…/memory/`) and auto-load every session. It moved so
memory rides with the commit; the old folder is a frozen backup. The
consequence is deliberate and worth stating: **nothing auto-loads any more**,
so memory that is not searched for is memory not used. `.claude/rules/
mnemo-memory.md` is the binding rule, and mnemo now dogfoods itself.

## Architecture map

One file, one owner — `docs/Memory-contracts-v3.md` §1 is the normative
table; do not edit a module you do not own.

Engine (the `.md → vectors` pipeline):

- `src/config.py` — paths, model, knobs. **Sectional ownership** (§1.1):
  edit only your section.
- `src/chunker.py` — heading-aware splitting; `start_char`/`end_char`.
- `src/embedder.py` — fastembed (ONNX), `multilingual-e5-large`.
- `src/providers/` — `base.py` embedding-provider interface, `local.py`
  over the resident daemon (`api.py` arrives at phase 7).
- `src/embed_server.py` — warm resident model daemon (loopback TCP).
- `src/store.py` — sqlite-vec + FTS5 + hashes + `meta`; flat schema, no
  `scope`/`agent_name`; an incompatible schema is dropped and rebuilt.
- `src/index.py` — walk + sha256-diff + reindex changed + prune.
- `src/search.py` — vector kNN + FTS5 + RRF + optional `path_prefix`.

Service (the persistent backend):

- `src/registry.py` — banks registry (`state/banks.json`); resolve by
  id, name or nested path.
- `src/servicelog.py` — `service.db`: query + index events, retention.
- `src/api.py` — FastAPI/uvicorn loopback host. Two surfaces, and the
  split is deliberate: **private** `/api/*` for the cabinet (`/search`,
  `/reindex`, `/tree`, `/status`, `/banks`, `/fs/dirs`, `/file`, `/logs`
  — hidden from OpenAPI), and **external** `/mcp-tools/<tool_name>`, the
  three MCP tools as plain HTTP for a human with curl or Swagger. Bearer
  token on both; `/mcp-tools` also takes `?token=`.
- `src/workqueue.py`, `src/watcher.py` — priority queue + worker and the
  watchdog→debounce→enqueue path (**phase 3, in flight**).
- `src/service_ctl.py` — `mnemo service …`, windowless spawn, PID/port
  state (**phase 5, in flight**).
- `src/webui/` — the local cabinet served by the backend; `devserver.py`
  answers contract shapes from fixtures and is a dev tool only.

Faces:

- `src/mcp_server.py` — **FastMCP** (from the official `mcp` SDK), mounted
  into `api.py` at `/mcp`. FastAPI only hosts it; the two frameworks are
  nested, not mixed. Tool bodies live in module-level `run_search` /
  `run_tree` / `run_reindex` so `/mcp-tools/*` mirrors them by *calling*
  them — the mirror cannot drift from the tool.
- `src/cli.py` — thin client of the API (`src/client.py`); `warmup`,
  `init`, `doctor` stay local. Hook targets `memory-hook` (SessionStart)
  and `hook-inject` (UserPromptSubmit) are **seeds**: working commands
  that nothing wires automatically.
- `src/scaffold.py` — `mnemo init`: additive, idempotent, refuses on
  conflict. Writes **no hook** unless `--with-memory-hook` /
  `--with-inject-hook`; `--migrate` unwires what was not asked for. Owns
  `_MEMORY_RULE`, the text that lands in adopted repos as
  `.claude/rules/mnemo-memory.md`.

Around it:

- `install.sh` — engine installer, POSIX (`--check`/`--home`).
- `install.ps1` — same for native Windows (`-Check`/`-InstallHome`/
  `-Python`/`-DepsOnly`; PowerShell 5.1+, 64-bit Python 3.10+).
- `tests/test_search.py` — labeled recall eval (regression floor);
  `tests/test_platform.py`, `tests/test_install_windows.py` — wiring and
  installer; `tests/test_mcp.py` — MCP over HTTP against the running
  service, plus the check that `/mcp-tools/*` is byte-identical to the
  tools it mirrors. The bundled fixture corpus uses the canonical layout
  (`.claude/memory/` with `logs/`, `agents/<role>/` inside).
- Installed engine: `~/.claude/mnemo/` (`bin/mnemo`, a real `bin\mnemo.exe`
  on Windows, `.venv`, `model-cache`, `state/`). Project wiring: `.mcp.json`,
  `.claude/settings.json`. Adoption skill + its bundled templates:
  `.claude/skills/mnemo-adopt/`.

## Commands

Landed and working today:

```
mnemo warmup                        one-time explicit ~2.2 GB model download + check
mnemo init [--root DIR]             additive, idempotent project wiring; NO hook
     [--migrate]                    also unwire hooks mnemo no longer writes
     [--with-memory-hook]           wire the SessionStart seed (MEMORY.md + layout)
     [--with-inject-hook]           wire the UserPromptSubmit seed (top-N hits)
mnemo search "q" [--path-prefix P]  hybrid search over a bank
mnemo reindex [--bank B] [--full]   queue a reindex (`ingest` is a deprecated alias)
mnemo banks list|add|remove         registry, through the API
mnemo status | logs | tree | ui     service state, journal, tree, cabinet
mnemo memory-hook | hook-inject     hook seeds, not typed by hand
mnemo embed-server                  resident model daemon (auto-started)
```

There is no `mnemo mcp`: MCP is HTTP inside the running service, so a session
connects instead of spawning. Poke it by hand at `/mcp-tools/*` (Swagger at
`http://127.0.0.1:8918/docs`, token from `~/.claude/mnemo/state/api.token`).

Service control (`mnemo serve`, `mnemo service start|stop|status|restart`,
`mnemo autostart enable|disable|status`) is **phase 5, in flight** — the
subcommands exist before the lifecycle is verified. The API-client command
set (`banks`, `reindex`, `tree`, `status`, `logs`, `ui`, `doctor`) arrives
with phase 4; `docs/Memory-contracts-v3.md` §11.1 is the full target list.

`mnemo` is the launcher at `~/.claude/mnemo/bin/mnemo` (`bin\mnemo.exe` on
Windows) — it is NOT on PATH by default. Either call it by full path, or
add `~/.claude/mnemo/bin` to PATH / make a shell alias. The git-tracked
hooks and MCP always use the portable form (`~`/`${HOME}` resolved per
user; the extensionless path resolves to `.exe` on Windows), so they work
regardless.

## Updating the engine (after pulling new code)

The engine (`~/.claude/mnemo/`) is a mirror of this repo's `src/`, shared
by every project on the machine. It is decoupled from the repo: editing
`src/` here changes nothing until you re-run the installer. To roll out
an update:

```bash
# Linux / macOS
cd /home/dima/work_projects/other/mnemo   # the repo
git pull                                  # if updating from a remote
./install.sh                              # idempotent re-mirror + deps
./install.sh --check                      # optional: verify engine state
```
```powershell
# native Windows (PowerShell 5.1+)
cd E:\work_projects\other\mnemo           # the repo
git pull                                  # if updating from a remote
& .\install.ps1                           # idempotent re-mirror + deps
& .\install.ps1 -Check                    # optional: verify engine state
```

`install.sh` / `install.ps1` is idempotent and safe to re-run: it
re-mirrors `src/`, reinstalls deps (pip), and rewrites the launcher. It
**never** touches `state/` (per-project indexes) or `model-cache/`, so no
re-warmup and no re-index are needed for a code-only update. No skill is
required — this is a plain shell command, not a `mnemo` subcommand.

Extra steps only when:
- the embedding model changed in `src/config.py` → also run
  `~/.claude/mnemo/bin/mnemo warmup` and let the index rebuild;
- the wiring schema changed (hooks / `.mcp.json` shape) → re-run
  `~/.claude/mnemo/bin/mnemo init` in each adopted project (additive,
  idempotent — it only adds mnemo's own keys).

**While v3 is being built, re-mirroring is not a free action.** The
engine is shared by every project on this machine, and the v3 store
schema is incompatible: the first v3 run drops and rebuilds an index it
opens, and indexes keyed by a *project* root are simply orphaned — never
opened, never auto-deleted (a later `mnemo doctor` will list them for
explicit cleanup; until then they just sit there).
Refresh dependencies alone with `install.ps1 -DepsOnly`. From phase 5 the
order becomes **stop → refresh → start**, because the running backend
holds the venv's `python.exe`.

## Locked decisions (see the v3 docs for full rationale)

- Memory is organised into **flat banks**; isolation is a separate bank,
  never a scope inside one (design decision #13).
- The `.md` are the source of truth **in place**; git-tracking is a
  property of the location, not a mode (#1).
- Native, no Docker, in v1; Docker is a later Linux/server option (#2).
- Two long-lived processes: the backend (registry + index + watcher +
  API) and the model daemon holding the model warm (#4).
- `write` is not built — memory is edited only with native file
  tools (#6).
- Search never blocks: current index + status `indexing` / `empty` /
  `ready`, where `empty` (no index) ≠ no matches (#11).
- Hooks no longer reindex — the watcher does; hooks remain as optional
  auto-inject examples (#15).
- Embedding: `multilingual-e5-large` via fastembed, behind a pluggable
  provider interface.
- Vector search primary; FTS5/BM25 secondary; blended with RRF.
- The index is disposable and rebuilds deterministically from `.md`.
- The model is never downloaded implicitly — explicit `warmup` only.
- Everything on loopback, behind a token; nothing exposed outward
  without an explicit decision.

## Working rules

- **Step-by-step.** Stop and confirm at architectural forks. Never write
  code without explicit approval. Surface unexpected complexity instead
  of pushing through.
- **Conventional Commits.** Ask before committing/pushing.
- **Never** add `Co-Authored-By` or any attribution line.
- Comments and commit messages in English.
- Subagents do NOT inherit this file — any rule a subagent must follow
  belongs in its own agent file (see `templates/agents/`).
