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

---

# Persistent profile: reuse the host's index, share one resident

The ephemeral profile above rebuilds the index inside every container. That
first build is the whole cost of e5-large on CPU (~0.85 s/chunk, minutes for a
real memory tree). When containers for the **same** project come and go on one
host, a second profile avoids paying it every time: the container reads the
index the **host** already built, and reuses one resident model for embedding.

Two things make it work, both already in the engine.

## 1. Same path → same index

A project's index is keyed by `sha1(absolute project root path)`. So if the
project is mounted **at the same absolute path it has on the host**, the
container resolves the **same** `<projhash>.db`. Point
`MNEMO_STATE_DIR` at the host's `state/` mounted **read-write**, and the
container reads and updates the very file the host wrote.

```
HOST                                         CONTAINER
~/.claude/mnemo             ──:ro──►  ~/.claude/mnemo            (engine + model)
~/.claude/mnemo/state       ──:rw──►  ~/.claude/mnemo/state      (index — shared)
/abs/path/to/project        ──:rw──►  /abs/path/to/project       (same path!)
                                       └─ same sha1 → same <projhash>.db
```

The index lives on the host, so recreating the container never rebuilds it, and
`SessionStart → ingest` inside the container only hash-diffs the changed files
(seconds, well within the 60 s hook budget). `state` must be `:rw`, not `:ro`:
opening the store runs `PRAGMA` + `CREATE TABLE IF NOT EXISTS` every time, so
even a pure `search` writes.

**You own path stability.** Same path across containers → shared index
(concurrency-safe: the store uses WAL + `busy_timeout`, on a **local** fs — not
NFS). Distinct paths → one index per container. Pick deliberately.

## 2. One resident on the host, shared by containers

Instead of each container loading its own ~1.6 GB copy of the model, run one
resident on the host and let containers dial it:

| Env | Role | For this profile |
|-----|------|------------------|
| `MNEMO_EMBED_BIND` | address the resident **listens** on | `0.0.0.0` on the host (so both host loopback and containers reach it) |
| `MNEMO_EMBED_HOST` | address a client **dials** | `host.docker.internal` in the container |
| `MNEMO_EMBED_IDLE_TIMEOUT` | idle seconds before exit | `0` on the host resident — stay resident |

Defaults are `127.0.0.1` / `127.0.0.1` / `1800`, so nothing changes for the
plain single-machine case. A client only autostarts a resident it can own (one
on loopback); pointed at `host.docker.internal` it uses the host resident as-is
and, if that resident is down, degrades gracefully (search still works, it just
loads the model in-process that once) — it never spawns a hidden second copy.

Run the host resident as a **persistent service**, not a session-transient
process — a bare `systemd-run --user` unit tied to a shell gets reaped when that
shell exits. A System unit, a `--user` unit with `loginctl enable-linger`, or a
small container works:

```
[Unit]
Description=mnemo shared embedding resident
[Service]
Environment=MNEMO_EMBED_BIND=0.0.0.0
Environment=MNEMO_EMBED_IDLE_TIMEOUT=0
ExecStart=%h/.claude/mnemo/.venv/bin/python -m src.cli embed-server
WorkingDirectory=%h/.claude/mnemo
Restart=on-failure
[Install]
WantedBy=default.target
```

Bind `0.0.0.0` exposes the port on every interface — the token in
`state/embed.token` is a shared secret, not authentication, so keep the port off
the public network (firewall, or bind the docker bridge address instead).

A permanent resident never idle-exits, so ONNX's CPU memory arena — which is
freed on each restart in the plain profile — would otherwise accumulate; the
engine disables that arena (`perf(embedder): bound ONNX CPU memory arena`) so
resident RSS stays at the model footprint indefinitely.

A ready-to-adapt file lives next to this doc:
[`docker-compose.persistent.example.yml`](docker-compose.persistent.example.yml).

## Verified

- **Ephemeral profile** — read-only engine + read-only `model-cache` + ephemeral
  in-container state, then `ingest` and `search`, on a throwaway `ubuntu:24.04`
  container and a real `ccde:latest` dev image. `model-cache:ro` loads fine, so
  `:ro` is the right mount mode for fleets of workers.
- **Persistent profile** — end to end on `ubuntu:24.04` against the host's real
  voice-agent index: a container at the mirrored path resolved the same
  `<projhash>.db` and searched it with **no re-index**; destroying and recreating
  the container left the index intact; a matching-uid container wrote no
  root-owned files; the shared host resident embedded a container query in
  ~0.07 s.
