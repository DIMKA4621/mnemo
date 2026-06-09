# Running mnemo inside containers

Goal: dev/worker containers (spun up by the dozen, killed and recreated daily)
that can read and search project memory **without** dragging the 2.2 GB model
into every image and **without** littering the host with orphan index files.

## The model

mnemo has two decoupled layers (see the repo `CLAUDE.md`):

- **Engine** — code + venv + `model-cache` at `~/.claude/mnemo/`. Read-only,
  shared, identical for every container.
- **State** — the per-project SQLite index under `state/`. The *only* writable
  part, and the only thing that differs per run.

So the container recipe is: **mount the engine read-only from the host, point
the writable state at an ephemeral in-container path.**

```
HOST                                CONTAINER (own filesystem)
~/.claude/mnemo/   ──bind :ro────►  /root/.claude/mnemo/   (code + venv + model)
<repo>/            ──bind────────►  /workspace/<proj>/     (.md — source of truth)
                   MNEMO_STATE_DIR=/tmp/mnemo              (index — written here,
                                                            dies with the container)
```

The Python process runs **inside** the container. It reads its code and the
model through the read-only mount and writes the index to `MNEMO_STATE_DIR`,
which lives in the container's own (ephemeral) filesystem. Nothing it writes
ever touches the host.

## Why this shape

- **No garbage.** The index lives in the container; when the container is
  removed (even on `SIGKILL`), the index is gone. 10k workers/day leave zero
  orphan files on the host — no cleanup job, no randomized keys.
- **No collisions.** Each container has its own `state/`, so the path-based
  project hash can repeat across containers harmlessly — they never share a DB.
- **No re-download.** `model-cache` is mounted read-only and shared. Containers
  only read it; the model is never fetched implicitly (a hook never downloads).
- **Cheap startup.** The index is disposable and rebuilds from the mounted
  `.md` in seconds on `SessionStart → ingest`.

## What makes it work (engine knobs)

| Env | Meaning |
|-----|---------|
| `MNEMO_HOME` | Engine home. Defaults to `$HOME/.claude/mnemo`; set it if you mount the engine elsewhere than the container's `$HOME`. |
| `MNEMO_STATE_DIR` | **The key knob.** Relocates *only* the writable state (index + logs + embed token) without moving the engine or model-cache. Point it at an ephemeral container path. |
| `MNEMO_ROOT` | Pins the project root (the dir whose `.claude/memory` is indexed) regardless of the process cwd. |

`MNEMO_STATE_DIR` is what splits "read the model from the host" from "write the
index into the container". Without it, the index and logs would try to land
under the read-only engine mount and fail.

## Prerequisite

The **host** must be installed and warmed once so the model is present:

```
./install.sh
~/.claude/mnemo/bin/mnemo warmup    # one-time ~2.2 GB download
```

Containers only ever read that `model-cache`.

## Minimal `docker-compose.yml`

A ready-to-adapt file lives next to this doc:
[`docker-compose.example.yml`](docker-compose.example.yml).

```yaml
services:
  worker:
    image: your-worker-image          # needs python3.12 + libgomp1
    volumes:
      - ${HOME}/.claude/mnemo:/root/.claude/mnemo:ro   # engine + venv + model (read-only)
      - ./project:/workspace/proj:rw                     # the .md memory (source of truth)
    environment:
      MNEMO_STATE_DIR: /tmp/mnemo   # ephemeral index — dies with the container
      MNEMO_ROOT: /workspace/proj
    tmpfs:
      - /tmp/mnemo                   # back the ephemeral index with RAM (optional)
```

Run a one-off ingest or search through compose:

```bash
docker compose run --rm worker \
  /root/.claude/mnemo/bin/mnemo search "your query" --root /workspace/proj
```

### Base-image compatibility

The mounted venv shares its stdlib from the container's **system** interpreter
at `/usr/bin/python3`, so the base image must provide:

- **Python 3.12.x at `/usr/bin/python3`** — the same minor as the host venv
  (built for the `cp312` ABI). The patch level need not match: a host venv from
  Python 3.12.3 runs fine in an image carrying 3.12.13.
- **`libgomp1`** — required by onnxruntime.

Verified working: `ubuntu:24.04` (after `apt-get install -y python3 libgomp1`)
and an Ubuntu 22.04 dev image that already ships `python3.12` + `libgomp1`.

Images without Python, or with a different minor (3.10/3.11), cannot host the
mounted venv — install `python3.12` into the image, or build the engine inside
it with `install.sh`. (Note: application/worker images that do **not** run
Claude Code don't need any of this; only the container where Claude Code and
its hooks run needs the engine.)

## Verified

Checked end to end — read-only engine + read-only `model-cache` + ephemeral
in-container state, then `ingest` and `search` — on both a throwaway
`ubuntu:24.04` container and a real `ccde:latest` dev image. Result:
**`model-cache:ro` loads fine** (no write into the shared model is needed), so
`:ro` is the right mount mode for fleets of workers.
