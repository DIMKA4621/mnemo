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
- Index: one SQLite file per bank at `~/.mnemo/state/<bankhash>.db`
  — gitignored realm, deletable, fully rebuildable from the `.md`.
- Access: **one persistent local service** owns the registry, the index
  and the watcher; CLI, MCP, hooks and the web console are thin clients
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
the `mnemo-memory` MCP server in `.mcp.json`. **Search it with the `search` tool
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
- `src/embedctl.py` — backend **memory**: what holds a model right now, and
  giving it back. Not an off switch — a backend that is off is a fault, not
  a mode; the model returns on the next search, paying ~7–8 s once, which is
  the trade `MNEMO_EMBED_IDLE_TIMEOUT = 0` deliberately left to a command
  rather than a timer. `local` stops the resident (~1.5 GB, found by port and
  by our token, never by a PID we think we spawned); Ollama gets
  `keep_alive: 0` **on its native `/api/embed`** — the OpenAI-compatible
  `/v1/embeddings` accepts that field and silently ignores it (measured: 200,
  a correct vector, and the model still resident), so routing this through
  `ApiProvider` would ship a button that reports success and frees nothing.
  **Only our own model is unloaded**; other models there belong to whoever
  loaded them and are counted, never named.
- `src/store.py` — sqlite-vec + FTS5 + hashes + `meta`; flat schema, no
  `scope`/`agent_name`; an incompatible schema is dropped and rebuilt.
- `src/index.py` — walk + sha256-diff + reindex changed + prune.
- `src/search.py` — vector kNN + FTS5 + RRF + optional `path_prefix`.

Service (the persistent backend):

- `src/registry.py` — banks registry (`state/banks.json`); resolve by
  id, name or nested path. A bank carries **one** `state` —
  `enabled | frozen | disabled` — not a pair of booleans, since two fields
  describing one fact are free to disagree the moment somebody hand-edits
  the file. `frozen` is the useful middle: **not watched, still
  searchable**, so changing the machine's embedding backend no longer costs
  a rebuild of every bank on it. `Bank.enabled` survives as a computed
  property (`watched` / `searchable` are the other two), which is why the
  dozen call sites reading it needed no change and why nothing can assign
  it. The pre-`state` boolean is still read (`false` → `disabled`) and
  disappears on the next write.
- `src/servicelog.py` — `service.db`: query + index events, retention.
- `src/api.py` — FastAPI/uvicorn loopback host, and the router that
  decides which credential opens which face. **Private** `/api/*` for the
  console (`/search`, `/reindex`, `/tree`, `/status`, `/banks`,
  `/banks/{id}/token`, `/fs/dirs`, `/file`, `/logs`, `/settings`, `/embed/*`,
  `/autostart`, `/doctor`, `/clean-orphans` — hidden from
  OpenAPI); **external** `/mcp-tools/<tool_name>`, the tools as plain
  HTTP for a human with curl or Swagger; and the two mounted MCP faces,
  `/mcp` and `/mcp-admin`. `/mcp`, `/mcp-admin` and `/mcp-tools` also take
  `?token=`, because an MCP client configures a URL, not headers.
  `/mcp-tools/*` and `/mcp-admin` take the **service** token; `/mcp` takes
  a **bank** token and nothing else. `/api/*` — the console's and CLI's own
  channel — takes the service token **only if one has been deliberately
  configured** (`$MNEMO_API_TOKEN`, or a future opt-in "generate" step);
  with none configured, the default, `/api` is open (2026-08-21 decision:
  it's loopback-only, and a login token bought no real security there while
  costing every `mnemo ui` open a "paste the token" screen). A leftover
  `/mcp/<bank>` path is a 400 pointing at `init --migrate`, not a segment
  quietly ignored.
- `src/workqueue.py`, `src/watcher.py` — priority queue + worker and the
  watchdog→debounce→enqueue path (**phase 3, in flight**).
- `src/service_ctl.py` — `mnemo service …`, windowless spawn, PID/port
  state (**phase 5, in flight**).
- `src/webui/` — the local console served by the backend; `devserver.py`
  answers contract shapes from fixtures and is a dev tool only, so it grows a
  route whenever the real API does or dev mode breaks. **Every per-bank action
  lives in one `···` menu** at the right end of the card's title row —
  reindex, full rebuild, MCP access, remove — and the card carries no buttons
  of its own. That row of buttons was what pinned the column's width from
  both sides (under 287px it wrapped, over 311px the document ended up
  narrower than the file list); with only a glyph to fit, the constraint is
  gone and every card is four lines shorter. Removal is the only irreversible
  action here, and its dialog leads with the **token**, not the megabytes:
  the index rebuilds, the token is minted and cannot be reissued.
  Machine settings are a **screen**, not a modal (the gear in the topbar) —
  everything else here is about one bank and floats over the work; this is
  about the machine and replaces it. The backend is **picked, not typed**:
  the tabs come from `presets`, and choosing a model fills in its URL, width
  and prefixes together. That is the point rather than a convenience — a
  prefix field is a field somebody forgets, which is the same silent failure
  the catalogue exists to remove. Provider changes apply **without a service
  restart**; a warning banner names every `REBUILD PENDING` bank and queues the
  existing full-reindex action after one confirmation (disabled and already-
  indexing banks are never duplicated). «Обслуговування» renders the structured
  doctor report and removes only the orphan ids just shown, after an inline
  confirmation. Hosted APIs get a metered «Перевірити ендпоінт» probe, never an
  unload button.

Faces:

- `src/mcp_server.py` — the **project** face: **`MCPServer`** (from the
  official `mcp` SDK) mounted into `api.py` at `/mcp`. FastAPI only hosts
  it; the two frameworks are nested, not mixed. **Read-only, two tools —
  `search(query, top_k, path_prefix)` and `tree` — and no `bank`
  parameter on either.** The bank comes from the presented token and from
  nothing else: the URL segment, the `X-Mnemo-Bank` header and the "if
  there is only one bank" rule are all gone, each because it was a second
  thing saying which bank, free to disagree with the credential. Tool
  bodies live in module-level `run_search` / `run_tree` / `run_reindex`
  so `/mcp-tools/*` mirrors them by *calling* them — the mirror cannot
  drift from the tool — and those keep their `bank` parameter, since the
  mirror sits behind the service token and naming a bank is its point.
- `src/mcp_admin.py` — the **admin** face, mounted at `/mcp-admin` under
  a distinct server name (`mnemo-admin`, so tools namespace as
  `mcp__mnemo-admin__reindex`). Service token only. Tools: `banks`,
  `bank_add`, `bank_remove`, `bank_state`, `reindex`, `status`, `logs`.
  `reindex` lives here rather than on a project face because the watcher
  reindexes on its own within seconds of a save.
- `src/cli.py` — thin client of the API (`src/client.py`); `warmup`,
  `init`, `doctor`, `clean-orphans` stay local. **No hook targets any
  more** beyond the `hook-postedit` no-op shim: the discipline lives in
  the rule, not in an injection.
- `src/diagnostics.py` — one structured `doctor` report shared by CLI and
  console. CLI renders text; `GET /api/doctor` returns the same facts; neither
  echoes credentials nor probes a metered endpoint. Owns race-safe explicit
  orphan cleanup for both CLI and `POST /api/clean-orphans`.
- `src/scaffold.py` — `mnemo init`: additive, idempotent, refuses on
  conflict. Registers the project's memory root as a bank and writes
  **that bank's literal token** into the wiring. It **builds the template
  layer in every project**: `.mcp.json.template` (placeholders, tracked),
  `.mcp.env` + `.mcp.env.example`, and both `mcp-setup.sh` and
  `mcp-setup.ps1` — two halves because bash is not a thing a native
  Windows machine has, held to byte-identical output by a test. Those
  scripts **discover** their substitutions from the template instead of
  listing one `sed -e` per placeholder, which is what removes the layer's
  only silent failure: a placeholder with no value is now a named error
  and no file written, where it used to be copied through verbatim while
  the script exited 0. A new bank is therefore an entry in the template
  plus a token in `.mcp.env`, nothing else. The template **starts as the
  project's existing `.mcp.json`**, or converting to the layer would drop
  every other server it had. A tracked `.mcp.json` / `.mcp.env` is not a
  refusal any more: `init` explains, asks, and runs `git rm --cached`
  itself (`--yes` for scripts; **without a terminal it does nothing** and
  prints the command). After seeding memory it also runs read-only
  `git check-ignore -v`: a broad `**/.claude` no longer lets `init` promise
  portability while git silently drops the bank. It prints the matching rule
  and narrow exceptions, but never rewrites the project's broad ignore policy.
  Writes **no hook at
  all**, and no flag makes it write one; `--migrate` unwires every hook
  mnemo ever wrote and rewrites the superseded `/mcp/<bank>` URL. The
  `mcpServers` key it writes is **`mnemo-memory`** (tools namespace as
  `mcp__mnemo-memory__search`), leaving room for a second entry on another
  bank; an older `mnemo` key it authored is **renamed, not joined** — by a
  plain run when the entry is already HTTP, by `--migrate` when it is still
  stdio. A server somebody else called `mnemo` is left alone.
  Owns `_MEMORY_RULE`, the text that lands in adopted repos as
  `.claude/rules/mnemo-memory.md` — and **refreshes it in place** when the
  file's sha256 is one mnemo itself wrote (`_RULE_SUPERSEDED`, two digests per
  redaction because pre-`_write` Windows adoptions got CRLF). An unrecognised
  digest is somebody's edit and outranks the update; `MEMORY.md` is curated
  content and is never reconsidered. Also owns `adopted_projects()` /
  `known_project_roots()`, which find the projects on this machine carrying
  mnemo wiring — from `~/.claude.json`'s `projects` map, since
  `~/.claude/projects/` folder names flatten `:`, `\` and `_` all to `-` and
  cannot be decoded back.

Around it:

- `install.sh` — engine installer, POSIX (`--check`/`--home`).
- `install.ps1` — same for native Windows (`-Check`/`-InstallHome`/
  `-Python`/`-DepsOnly`; PowerShell 5.1+, 64-bit Python 3.10+).
- `tests/test_search.py` — labeled recall eval (regression floor);
  `tests/test_platform.py` — wiring and scaffold plans;
  `tests/test_pipeline.py` — the whole `.md → chunks → vectors → search`
  path on a throwaway bank behind a hash provider, so CI exercises it
  without the 2.2 GB model; `tests/test_install_windows.py` and
  `tests/test_install_posix.py` — each installer/uninstaller pair run for
  real against a throwaway `--home`, one per platform because they share
  no code; `tests/test_mcp.py` — MCP over HTTP against the running
  service, plus the check that `/mcp-tools/*` is byte-identical to the
  tools it mirrors. The bundled fixture corpus uses the canonical layout
  (`.claude/memory/` with `logs/`, `agents/<role>/` inside).
- Installed engine: `~/.mnemo/` (`bin/mnemo`, a real `bin\mnemo.exe`
  on Windows — a subprocess dispatcher resolving itself from `sys.argv[0]`,
  never `sys.prefix`, and spawning `current/.venv/.../python -m src.cli`;
  `versions/<tag>/{src,.venv}` one full tree per installed release,
  `current` a junction/symlink naming the active one; `model-cache` and
  `state/` shared across versions). Project wiring:
  `.mcp.json` — **git-ignored**, since it holds a bank token — or the
  `.mcp.json.template` / `.mcp.env` / `mcp-setup.sh` layer where a project
  uses that convention, plus `.claude/settings.json`. Adoption skill + its
  bundled templates: `.claude/skills/mnemo-adopt/`.

## Commands

Landed and working today:

```
mnemo warmup [--force]              one-time explicit ~2.2 GB model download + check;
                                    skips when nothing here embeds locally
mnemo init [--root DIR] [--yes]     additive, idempotent project wiring; NO hook
     [--migrate]                    also unwire every hook mnemo ever wrote
mnemo search "q" [--path-prefix P]  hybrid search over a bank
mnemo reindex [--bank B] [--full]   queue a reindex (`ingest` is a deprecated alias)
mnemo banks list|add|remove         registry, through the API
mnemo banks freeze|unfreeze         a bank's state: frozen keeps it searchable
     |disable <ref>                 while it stops following its files
mnemo status | logs | tree | ui     service state, journal, tree, console
mnemo embed [status|unload|load]    what the backend holds in memory, and give
                                    it back — NOT an off switch
mnemo doctor                        engine, provider, model, tokens, ports, banks,
                                    orphans, and the projects needing rewiring
mnemo clean-orphans [--dry-run]     delete index files no bank claims; asks first
     [--yes]                        skip the prompt (scripts)
```

Five more are **hidden from `--help` but still work**, because nothing types
them and something calls them: `serve` (what `service start` spawns),
`embed-server` (what the backend spawns), `hook-postedit` (a no-op that
exists *only* so an already-wired v2 hook does not fail), `ingest`
(deprecated alias, still warns), and `update-apply` (engine self-update's
`stop → switch current → start → health-gate → rollback`, spawned detached
by the console's apply button; also runnable by hand for diagnostics — exit
0/1/2/3 = applied / rolled back / nothing staged / both apply and rollback
failed, service down). The two hook seeds are **gone**, not
hidden. Hidden by omitting `help=` on the subparser: `argparse.SUPPRESS`
there prints `==SUPPRESS==` instead of hiding.

A bank defaults to the current directory, resolved **both ways** — the bank
containing it, or the one bank it contains, so `mnemo search` works from a
project root even though memory lives at `<project>/.claude/memory`. Several
banks under one path is an error naming them, never a guess. A relative
`--bank` path is made absolute by the CLI, since the backend's cwd is its own.

There is no `mnemo mcp`: MCP is HTTP inside the running service, so a session
connects instead of spawning. Two faces, keyed by which token is presented —
`/mcp?token=<bank-token>` (project, read-only: `search`, `tree`) and
`/mcp-admin?token=<service-token>` (`banks`, `bank_add`, `bank_remove`,
`bank_state`, `reindex`, `status`, `logs`). Neither credential opens the
other's face.
Poke the read tools by hand at `/mcp-tools/*` (Swagger at
`http://127.0.0.1:4646/docs`, token from `~/.mnemo/state/api.token`).

**`local` is not the only provider, and three commands branch on that.**
Under `api` the model cache is empty by design and the resident never starts,
so `doctor` says `not needed under \`api\`` and `n/a` instead of reporting them
as findings — otherwise the first command anyone runs when something breaks
opens with a permanent false alarm. It prints the endpoint (url, model, dim,
whether a key is set) but **never calls it**: a diagnostic that embedded a
probe would cost money on a metered API and burn a rate limit, and `doctor`
gets run repeatedly while fixing things. `warmup` skips the 2.2 GB download
for the same reason and prints `--force`. The question is the **union** of
the machine setting and every bank's own `provider` field — a bank may name
`local` while the machine is on `api`. `status` changes the word, not the
check: `reachable`/`DOWN` under `local` (a real process), `configured`/`not
configured` under `api` (nothing was called) — `embed.kind` in the status
payload is what says which.

Service control (`mnemo serve`, `mnemo service start|stop|status|restart`,
`mnemo autostart enable|disable|status`) is **phase 5, in flight** — the
subcommands exist before the lifecycle is verified. The API-client command
set (`banks`, `reindex`, `tree`, `status`, `logs`, `ui`) arrives with phase
4; `doctor` and `clean-orphans` are **local** — they read this machine's
state directory and must still work with the backend down.
`docs/Memory-contracts-v3.md` §11.1 is the full target list.

`mnemo` is the launcher at `~/.mnemo/bin/mnemo` (`bin\mnemo.exe` on
Windows) — it is NOT on PATH by default. Either call it by full path, or
add `~/.mnemo/bin` to PATH / make a shell alias. The git-tracked
hooks and MCP always use the portable form (`~`/`${HOME}` resolved per
user; the extensionless path resolves to `.exe` on Windows), so they work
regardless.

## Installing from scratch

Two levels, and they cannot collapse into one: the installer sets up **the
machine**, `init` attaches **one project**, and only you know which
directory that is.

```powershell
git clone https://github.com/DIMKA4621/mnemo.git   # native Windows
cd mnemo
& .\install.ps1        # venv, deps, launcher, token, profile, autostart,
                       # then: model (asks) -> service start -> doctor
cd <your project>
& "$HOME\mnemo\bin\mnemo.exe" init
```
```bash
git clone https://github.com/DIMKA4621/mnemo.git   # Linux / macOS
cd mnemo && ./install.sh
cd <your project> && ~/.mnemo/bin/mnemo init
```

The installer ends on **evidence, not a promise**: it runs `doctor` and
prints the result, so the last thing on screen is the engine reporting its
own state. Escape hatches for anyone who wants the steps back:
`-NoModel`/`--no-model`, `-Model`/`--model` (no prompt, for scripts),
`-NoStart`/`--no-start`, plus `-Check`, `-DepsOnly`, `-NoAutostart`.

The model prompt is how the "never downloaded implicitly" invariant
survives a one-command install: consent stays explicit, it just happens in
the same sitting. **A non-interactive run never prompts** — it skips and
prints the command, because a prompt nobody sees either hangs forever or
reads a byte of piped data as the answer.

A custom `-InstallHome`/`--home` is an isolated copy (the test suite uses
one) and gets **none** of the user-scope part: no token export, no profile,
no autostart, no model, no service, no `doctor`.

## Removing it (the mirror image)

```powershell
& .\uninstall.ps1        # or: ./uninstall.sh on POSIX
```

Takes away exactly what the installer put on the machine — service and
resident, autostart, the profile block, `MNEMO_API_TOKEN`, and the engine
home with `state/` and `model-cache/` — after printing the list and asking.
`-DryRun`/`--dry-run` shows the list and stops; `-KeepModel`/`--keep-model`
and `-KeepState`/`--keep-state` spare the expensive parts; `-Yes`/`--yes`
skips the prompt, and **without a terminal it removes nothing** rather than
guessing (same reasoning as the model prompt, opposite default).

Every step is independent and best-effort, because an uninstaller is reached
for when something is already broken: a missing task, a missing launcher or a
half-deleted venv is reported, not fatal; exit 1 only if something that
exists could not be removed. Stopping goes **through the launcher**
(`service stop` knows the fingerprint check); signalling the recorded PIDs is
the fallback for an engine too broken to have one.

Nothing under a project is touched — the `.md` are the source of truth and
only derived state goes. Deliberate survivors: `HOME` (the installer only
sets it when absent and keeps no record, so git or ssh may rely on it now)
and Linux lingering. The one thing that does not survive and cannot be
rebuilt from the `.md` is `state/banks.json`, so the uninstaller prints every
bank and its root first, and each project needs `mnemo init` again after a
reinstall.

**A from-scratch install is the only thing that tests the from-scratch
promise.** Re-running the installer over an existing engine proves nothing
about a clean machine: it keeps the venv that was already resolved. That is
how an unbounded `mcp>=1.0.0` shipped a broken fresh install while every
existing machine kept working (design decision #29).

## Updating the engine (after pulling new code)

The engine (`~/.mnemo/`) is a mirror of this repo's `src/`, shared
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

`install.sh` / `install.ps1` is idempotent and safe to re-run: it builds
`versions/local/` fresh from this checkout, repoints `current` at it, and
republishes the `bin/` launchers — reinstalling deps along the way. It
**never** touches `state/` (per-project indexes) or `model-cache/`, so no
re-warmup and no re-index are needed for a code-only update. No skill is
required — this is a plain shell command, not a `mnemo` subcommand.

**This git-pull path is one of two ways the engine updates now.** The other
is self-update from the console (design decision #33, `docs/
Memory-contracts-v3.md` §9.9): the backend checks GitHub tags on a timer (or
on demand), and a click stages and applies a *tagged release* under
`versions/<tag>/` with a health-gated rollback if the new build doesn't come
up healthy. That path is for end users running a released `mnemo` — it pulls
nothing from a local checkout and knows nothing about uncommitted work. This
manual path stays the one for developing mnemo itself: it always rebuilds
the fixed `versions/local/` from whatever is in `src/` right now, committed
or not.

Extra steps only when:
- the embedding model changed in `src/config.py` → also run
  `~/.mnemo/bin/mnemo warmup` and let the index rebuild;
- the wiring schema changed (hooks / `.mcp.json` shape) → re-run
  `~/.mnemo/bin/mnemo init` in each adopted project (additive,
  idempotent — it only adds mnemo's own keys), with `--migrate` where the
  project still carries an older mnemo-authored entry. If `.mcp.json` is
  git-tracked there, `init` refuses until `git rm --cached .mcp.json` —
  it will not write a bank token into a tracked file.

**Coming from v2, the installer notices and says so.** A v2 engine is
recognised by what v2 never had — a banks registry — so an absent
`state/banks.json` next to `.db` files is unambiguous, and safe to act on:
with no registry, no index can belong to a live bank. Those indexes are
orphaned the instant v3 runs (v2 keyed them by *project* root, v3 by *bank*
root) and go via `clean-orphans --yes`. Projects it only **names**: `doctor`
ends with a `project wiring` section listing each one and the exact command,
because they are someone else's working trees. Three ways in: a legacy shape
(stdio entry, retired hook) needing `--migrate`; no registered bank covering
the project; or a token from a previous life — tokens are minted, so a
reinstall gives the same bank a new secret while the project keeps the old
one, and from inside that project nothing shows it.

**While v3 is being built, re-mirroring is not a free action.** The
engine is shared by every project on this machine, and the v3 store
schema is incompatible: the first v3 run drops and rebuilds an index it
opens, and indexes keyed by a *project* root are simply orphaned — never
opened, never auto-deleted. `mnemo doctor` counts them and `mnemo
clean-orphans` removes them, after showing the list and asking; nothing
deletes an index on its own.
Refresh dependencies alone with `install.ps1 -DepsOnly`.

**Do not stop the service by hand first** — the installer already does
`stop → refresh → start` itself, because the running backend holds the
venv's `python.exe`. It restores a service that was up, and on a clean
machine starts one that never was.

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
- The model is never downloaded implicitly by a hook, a service or a search
  — only `warmup`, or an install the user just ran themselves. The one-liner
  bootstrap (`get.ps1`/`get.sh`) is the one installer path that defaults to
  downloading it without asking, specifically because piping one command is
  the whole point of that path — `--no-model`/`-NoModel` opts out.
- Everything on loopback; nothing exposed outward without an explicit
  decision. `/mcp`, `/mcp-admin` and `/mcp-tools` stay behind a bearer
  token always. `/api` (console + CLI) is the one exception (2026-08-21):
  open by default on loopback, gated only once a token is explicitly
  configured — see `src/api.py`'s `auth_middleware`.

## Working rules

- **Step-by-step.** Stop and confirm at architectural forks. Never write
  code without explicit approval. Surface unexpected complexity instead
  of pushing through.
- **Conventional Commits.** Ask before committing/pushing.
- **Never** add `Co-Authored-By` or any attribution line.
- Comments and commit messages in English.
- Subagents do NOT inherit this file — any rule a subagent must follow
  belongs in its own agent file (see `templates/agents/`).
