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

## Getting started

Two commands, and they are at two different levels. The installer sets up
**the machine**; `init` attaches **one project**, and only you know which
directory that is.

```bash
git clone https://github.com/DIMKA4621/mnemo.git && cd mnemo
./install.sh                     # or: .\install.ps1 on Windows

cd /path/to/your/project
~/.claude/mnemo/bin/mnemo init   # or: & "$HOME\.claude\mnemo\bin\mnemo.exe" init
```

The installer creates the virtualenv, installs dependencies, writes the
launcher, exports the API token, registers `mnemo` in your shell profile,
registers autostart, offers to fetch the embedding model (~2.2 GB, asked
once), starts the service, and finishes by running `doctor` — so the last
thing on screen is the engine reporting its own state rather than a claim
that it worked. Re-running it is the update path, too: it stops the
service, re-mirrors the code, and brings it back.

If you want the steps back, they are all still there: `--no-model`,
`--model` (skip the prompt, for scripts), `--no-start`, `--check`,
`--deps-only`, `--no-autostart`, `--home DIR`.

Removal is the mirror image, and a separate script:

```bash
./uninstall.sh                   # or: .\uninstall.ps1 on Windows
```

It takes away exactly what the installer put on the machine — the service,
the autostart registration, the shell-profile block, `MNEMO_API_TOKEN`, and
the engine home with `state/` and `model-cache/` inside it — after printing
the list and asking. `--dry-run` shows that list and stops; `--keep-model`
and `--keep-state` spare the expensive parts; `--yes` skips the prompt for
scripts, and without a terminal it removes nothing rather than guessing.

Your markdown is never touched: it is the source of truth, and everything
that goes is derived from it. Two things survive on purpose — the `HOME`
variable (the installer only ever sets it when absent, and other tools may
rely on it now) and Linux lingering. One thing does not survive and cannot
be rebuilt from the `.md`: `state/banks.json`, the registry. After
reinstalling, run `mnemo init` again in each project, since the tokens in
their `.mcp.json` name a registry that no longer exists — which is why the
uninstaller prints every registered bank and its root before deleting it.

The engine installs on Linux, macOS and native Windows — `install.sh` on
POSIX, `install.ps1` (built-in PowerShell 5.1+, 64-bit Python 3.10+, no
WSL/PATH needed) on Windows. On Windows the installer sets the user
`HOME` if absent and refuses a value that differs from `%USERPROFILE%`,
so MCP and hooks resolve the same canonical path. After first creating
`HOME`, close and reopen the launching terminal or IDE before restarting
Claude Code. Everything below is identical across platforms.

Adoption leaves a little wiring in the project. Most of it is git-tracked
and travels with the repo; the exception is `.mcp.json`, which now carries
a credential and therefore must not.

- **`.mcp.json`** — registers the `mnemo-memory` MCP server over **HTTP**:
  `http://127.0.0.1:8918/mcp?token=<bank-token>`. A session *connects* to
  the running service — nothing is spawned, so no console flashes on
  Windows. Tools: `search(query, top_k, path_prefix)` and `tree` — short
  names on purpose, since Claude Code already namespaces them as
  `mcp__mnemo-memory__search`. The project face is **read-only**; `reindex` lives
  on the admin face, because the watcher reindexes within seconds of a save
  and a button nobody presses is a tool slot spent in every session.

  **There is no path segment and no bank name in that URL.** Every bank is
  minted its own token when it is registered, and that token is what tells
  the service which bank a connection belongs to — nothing else does. A
  second thing naming the bank could only ever disagree with the
  credential, and the failure it buys is the worst kind available: a
  request that succeeds against the wrong bank and looks entirely normal.
  What tells a *person* which bank an entry is for is the `mcpServers` key
  — `mnemo`, `mnemo-notes` — which is what one actually reads in a config.

  Because that token is literal, `.mcp.json` is **generated and
  git-ignored**. `mnemo init` adds the `.gitignore` line itself, and if the
  file is already tracked it **refuses** — writes nothing, and prints the
  `git rm --cached .mcp.json` to run first. The asymmetry is the reason: a
  refusal costs one command, whereas a token committed into a tracked file
  is in somebody else's clone by the time anyone notices.

  A project that keeps its MCP config under the template convention —
  `.mcp.json.template`, `.mcp.env.example` and `mcp-setup.sh` in git, the
  real values in a git-ignored `.mcp.env` — gets the placeholder form
  instead, and `init` detects that and writes into the template layer: the
  entry into the template, the variables into `.mcp.env.example` and
  `.mcp.env`, **and the matching `sed -e` lines into `mcp-setup.sh`**. That
  last one is not a nicety. A substitution that is missing leaves
  `{{MNEMO_TOKEN}}` sitting in the regenerated `.mcp.json`, while
  `mcp-setup.sh` prints its success tick and exits 0 regardless — the
  wiring is broken and nothing says so. Whoever clones such a project has
  no token at all (`.mcp.env` is not in git): open the cabinet (`mnemo
  ui`), copy the bank's token, paste it into `.mcp.env`, re-run
  `mcp-setup.sh`.
- **`.claude/rules/mnemo-memory.md`** — the binding memory rule, loaded
  for the main session and every subagent. Its body is deliberately
  portable: paste it into any agent's system prompt, point that agent at
  the MCP server, and the discipline travels.
- **`.claude/settings.json` — no hooks, and no way to ask for one.**
  Reindexing is the watcher's job, and memory is found by **searching, not
  by injection**. The two seeds that used to be available behind flags are
  gone: `hook-inject` delivered memory *before* the task was stated, which
  reads as memory already gathered, and `memory-hook` delivered an honest
  map — but the `tree` tool answers that on demand, and a map that arrives
  unasked competes with the rule telling the agent to go and look. The
  discipline now lives in exactly one place, which is the point: two
  mechanisms stating the same rule are two that can drift apart.

  `mnemo init --migrate` is the upgrade path for a project adopted under an
  older shape: it unwires hooks earlier versions added, and rewrites a
  `.mcp.json` still pointing at the superseded `/mcp/<bank>?token=…` URL.
  That URL now answers 400 rather than silently routing, and says so.

  `init` also refreshes `.claude/rules/mnemo-memory.md` when the file is
  still byte-for-byte one that mnemo wrote — the rule text has grown, and a
  project adopted months ago would otherwise keep months-old rules with
  nothing saying so. Edit that file and it is yours: an unrecognised digest
  is left exactly as it is.

### Coming from v2

Run the installer as usual. It recognises a v2 engine by what v2 never had —
a banks registry — and retires its indexes, which are orphaned the moment v3
runs: v2 keyed them by *project* root, v3 keys them by *bank* root, so none
can be reused. Nothing under a project is touched.

What it will not do is edit your projects. v2 kept no registry of them, and
the list cannot be recovered from the indexes either — the filename is
`sha1(root)`, which does not invert, and a v2 database has no `meta` table to
ask. So `doctor` ends with a `project wiring` section that lists every project
this machine can still find and the exact command for each:

```
project wiring   2 of 5 project(s) need rewiring
  mnemo init --migrate --root "/home/you/work/api"
      .mcp.json: mnemo is a L1 stdio entry +3 more
  mnemo init --root "/home/you/work/site"
      no registered bank covers it
```

The same section catches a subtler case after any reinstall: the project's
wiring is current and points at a bank that exists, but the token in it is
from a previous life. Tokens are minted rather than derived, so a rebuilt
registry gives the same bank a new secret — and from inside that project
nothing shows it, the session simply finds no memory tools.

Memory itself lives at **`.claude/memory/`** — `MEMORY.md` as a thin index,
`logs/` for day notes, `topics/` one concept per file, `agents/<role>/` for
per-role memory. Everything nests under that one root, which is why the bank
never swallows your `skills/`, `rules/` or `agents/`: the boundary is the
folder, so there is no exclusion list to maintain.

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
mnemo init [--root DIR]      # additive, idempotent project wiring; no hooks
mnemo search "query" [--path-prefix SUBFOLDER] [-k N]
mnemo reindex [--bank B] [--path P] [--full]
mnemo banks list | add <path> | remove <ref>
mnemo status | logs | tree | ui
mnemo service start | stop | status | restart
```

There is **no `mnemo mcp`**: MCP is HTTP inside the running service, on two
separate faces. `/mcp?token=<bank-token>` is the **project** face — `search`
and `tree` over the one bank that token belongs to, and nothing else.
`/mcp-admin?token=<service-token>` is the **admin** face (server name
`mnemo-admin`, so its tools namespace as `mcp__mnemo-admin__reindex`): it
registers and drops banks, forces a reindex and reads the journal —
`banks`, `bank_add`, `bank_remove`, `reindex`, `status`, `logs`. The two
credentials do not cross. A bank token on `/mcp-admin` is a 401, and the
service token on `/mcp` is a 401 too — it has no bank to resolve to — each
saying which face the caller wanted.

To poke the read tools by hand, use the mirror — plain request/response, no
JSON-RPC:

```bash
TOKEN=$(cat ~/.claude/mnemo/state/api.token)
curl "http://127.0.0.1:8918/mcp-tools/search?bank=NAME&query=rollback&token=$TOKEN"
```

It returns exactly what the agent reads (`&format=json` wraps that same
string). Swagger for it: `http://127.0.0.1:8918/docs` — click **Authorize**
and paste the token. The cabinet's own `/api/*` is private and deliberately
absent from that page.

`serve`, `embed-server`, `hook-postedit` and `ingest` exist too but are
hidden from `--help`: nothing types them, something calls them. `$MNEMO_ROOT` overrides the bank root; `--root`
defaults to the current directory.

The v2 `--scope project|agent` / `--agent NAME` flags are **gone**: banks
are flat, and `--path-prefix` replaces them with any folder depth. The
first v3 run also rebuilds the index — the schema changed, and rebuilding
from the `.md` is the migration. Older index files keyed by a project
root are left alone: never opened, never deleted for you.

Service control, banks management and the web cabinet arrive with the
later v3 phases; the target command set is listed in
`docs/Memory-contracts-v3.md` §11.1.

## Run in a container

> **⚠ Superseded — do not follow this section as written.** It describes the
> **v2** model, where the CLI drove the engine directly inside the container.
> It no longer does: `search`, `tree`, `reindex` and `logs` are clients of the
> running service and address a bank with `--bank`, so the `--root` commands
> below fail with an argparse error and there is no service in the container
> for them to reach. How a container gets a service and a bank is an open
> decision and the recipe is being redesigned; the engine/state split it
> explains is still accurate, the commands are not. Full page, same caveat:
> [`docs/containers/`](docs/containers/README.md).

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
