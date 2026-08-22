#!/usr/bin/env bash
# mnemo engine installer — system scope, once per machine.
#
# This is THE machine-level command: one run takes a clean machine to a
# working, verified engine. Afterwards there is exactly one thing left, and
# only you can do it — `mnemo init` inside the project you want memory for,
# because the installer cannot know which directory that is.
#
# Deterministic and idempotent: safe to re-run to refresh code and deps. It
# NEVER touches the per-project index state, and it asks before downloading
# the embedding model.
#
#   ./install.sh              install or refresh, then warm, start, verify
#   ./install.sh --check      report engine state, change nothing
#   ./install.sh --deps-only  refresh venv packages only, leave src/ alone
#   ./install.sh --no-autostart  skip the systemd --user registration
#   ./install.sh --no-model   skip the model download
#   ./install.sh --model      download it without asking (scripts)
#   ./install.sh --no-start   do not start the service
#   ./install.sh --home DIR   install into DIR instead of the default
#
# Default location: $HOME/.mnemo  (override with $MNEMO_HOME).
set -euo pipefail

usage() {
	# Prints the header block above, however long it grows. It used to be a
	# fixed line range, which silently truncated the moment a flag was added.
	awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' \
		"${BASH_SOURCE[0]}"
}

say() { printf 'install.sh: %s\n' "$1"; }
# Same as say(), highlighted -- for the one line the user actually needs to
# act on. Guarded on a real terminal: raw ANSI codes have nowhere to go once
# stdout is redirected (a log file, `curl | bash` with no tty) and would just
# show up as garbage text there.
say_hl() {
	if [ -t 1 ]; then
		printf 'install.sh: \033[33m%s\033[0m\n' "$1"
	else
		printf 'install.sh: %s\n' "$1"
	fi
}

run_with_heartbeat() {
	# Runs a command with a background dot-per-second heartbeat, so a slow
	# step (installing deps, downloading the model) never reads as a hang.
	# Output is captured to a temp log and only shown on failure -- a
	# successful run stays exactly as quiet as calling the command
	# directly, just not motionless. $1 is the label; the rest is the
	# command to run. Exit code is returned like any other command, so
	# callers under `set -e` behave exactly as an unwrapped call would.
	label="$1"; shift
	heartbeat_log="$(mktemp "${TMPDIR:-/tmp}/mnemo-install-XXXXXX.log")"
	printf 'install.sh: %s' "$label"
	"$@" >"$heartbeat_log" 2>&1 &
	heartbeat_pid=$!
	while kill -0 "$heartbeat_pid" 2>/dev/null; do
		sleep 1
		printf '.'
	done
	wait "$heartbeat_pid"
	heartbeat_code=$?
	printf ' done\n'
	if [ "$heartbeat_code" -ne 0 ]; then
		cat "$heartbeat_log" >&2
	fi
	rm -f "$heartbeat_log"
	return "$heartbeat_code"
}

file_size() {
	# One file's size in bytes. `stat` takes different flags on GNU and BSD,
	# and `wc -c` needs neither -- it just has to read the file.
	wc -c < "$1" 2>/dev/null | tr -d ' ' || printf '0'
}

human() {
	# Bytes -> a size a person reads. No `numfmt`: it is GNU-only and this
	# has to work on macOS too.
	awk -v b="${1:-0}" 'BEGIN {
		split("B KiB MiB GiB TiB", u, " ")
		i = 1
		while (b >= 1024 && i < 5) { b /= 1024; i++ }
		if (i == 1) printf "%d %s\n", b, u[i]
		else printf "%.1f %s\n", b, u[i]
	}'
}

# --- locate the repo (this script's own directory) ---------------------
SRC_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# A display-worthy version derived from this checkout's own git history,
# or nothing if no tag is reachable at all (no git, not a repo, no tags in
# history). Two outcomes, user-decided scheme (2026-08-22, mirrors
# install.ps1's Get-LocalCheckoutVersionTag):
#   - Clean tree, HEAD exactly on a tag -> that tag verbatim ("v3.0.1").
#   - Anything else with a reachable tag (commits on top of it, or
#     uncommitted changes) -> the nearest ancestor tag + lowercase "l"
#     ("v3.0.1l") -- a real base version instead of the uninformative bare
#     "local", marked as NOT the official release (lowercase, same
#     convention as alpha/beta suffixes). A checkout at v3.0.1 with local
#     edits on top is not actually v3.0.1 -- reporting it as plain
#     "v3.0.1" would make self-update think "already have it, nothing to
#     do" while running modified code (the class of "current lies about
#     what is actually running" bug engine_update.py's effective_current_tag
#     exists to close).
get_local_checkout_version_tag() {
	local repo_root="$1"
	command -v git >/dev/null 2>&1 || return 0
	(
		cd "$repo_root" || exit 1
		[ "$(git rev-parse --is-inside-work-tree 2>/dev/null)" = "true" ] || exit 1

		if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
			exact="$(git describe --tags --exact-match HEAD 2>/dev/null)" || exact=""
			if [ -n "$exact" ]; then
				printf '%s\n' "$exact"
				exit 0
			fi
		fi

		# --abbrev=0 gives just the nearest ancestor tag's name, no
		# "-N-gHASH" commit-count suffix -- exactly the base version this
		# scheme wants to append "l" to.
		nearest="$(git describe --tags --abbrev=0 HEAD 2>/dev/null)" || nearest=""
		[ -n "$nearest" ] && printf '%sl\n' "$nearest"
	) || true
}

# --- resolve the engine home and parse flags ---------------------------
DEFAULT_HOME="$HOME/.mnemo"
MNEMO_HOME="${MNEMO_HOME:-$DEFAULT_HOME}"
CHECK_ONLY=0
DEPS_ONLY=0
NO_AUTOSTART=0
NO_MODEL=0
WANT_MODEL=0
NO_START=0
while [ $# -gt 0 ]; do
	case "$1" in
		--check) CHECK_ONLY=1 ;;
		--deps-only) DEPS_ONLY=1 ;;
		--no-autostart) NO_AUTOSTART=1 ;;
		--no-model) NO_MODEL=1 ;;
		--model) WANT_MODEL=1 ;;
		--no-start) NO_START=1 ;;
		--home) shift; MNEMO_HOME="${1:?--home needs a directory}" ;;
		--home=*) MNEMO_HOME="${1#--home=}" ;;
		-h|--help) usage; exit 0 ;;
		*) echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
	esac
	shift
done

# Versioned layout (self-update, see .claude/memory/topics/
# engine-self-update-design.md): src/ and .venv live under
# versions/<tag>/, and `current` is a stable alias a switch repoints.
# state/ and model-cache/ stay siblings of versions/current -- shared
# across every version, never duplicated, never touched by anything
# below. The version tag is resolved in priority order so `mnemo doctor`/
# the console report the correct version instead of a permanent "local"
# that then nags to "update" back to the very tag already running:
#   1. $MNEMO_INSTALL_TAG -- set by get.sh when it downloaded a CONFIRMED
#      GitHub release (its own archive carries no .git directory, so this
#      is the only way this script can know). Used verbatim -- get.sh only
#      sets this when the checkout genuinely IS that exact release.
#   2. get_local_checkout_version_tag -- a manual git-based run: the exact
#      tag if HEAD sits on one with a clean tree, else the nearest ancestor
#      tag + a lowercase "l" (e.g. "v3.0.1l") to mark it as a local build
#      rather than the official release.
#   3. The fixed tag "local" -- the original, still-safe default when
#      neither of the above can answer (mid-development with no git
#      history reachable, no git at all, or get.sh's custom-archive/
#      explicit-ref override paths, none of which name a version). Real
#      release tags ("v3.1.0", ...) are also what a self-update apply
#      stages under versions/ (platform-dev's
#      install.ps1, mirrored on POSIX by nothing yet -- staging is
#      Windows-only for now, see the design topic's migration-risk
#      decision). Repeated runs of this script therefore refresh the SAME
#      versions/<tag>/ in place, exactly as idempotent as the old flat
#      layout was.
#
# This mirrors install.ps1's Build-EngineVersion / Set-CurrentVersion
# split structurally (same steps, same ordering) -- see that script's own
# comments for the fuller rationale behind each choice; not repeated line
# by line here. One thing genuinely differs and is called out where it
# matters below: bin/mnemo on POSIX is a plain resolving script (already
# updated for this layout), never a baked binary, so there is no
# Publish-Launchers equivalent here or in any future self-update apply --
# nothing here embeds a venv path at build time for retention to break.
VERSIONS_DIR="$MNEMO_HOME/versions"
VERSION_TAG="${MNEMO_INSTALL_TAG:-}"
if [ -z "$VERSION_TAG" ]; then
	VERSION_TAG="$(get_local_checkout_version_tag "$SRC_REPO")"
fi
VERSION_TAG="${VERSION_TAG:-local}"
VERSION_DIR="$VERSIONS_DIR/$VERSION_TAG"
CURRENT_LINK="$MNEMO_HOME/current"

PY_BIN="$CURRENT_LINK/.venv/bin/python"
LAUNCHER="$MNEMO_HOME/bin/mnemo"

line() { printf 'install.sh:   %-13s %s\n' "$1" "$2"; }

# Every runtime dependency the engine imports (v2 core + v3 service layer).
DEP_PROBE='import fastembed, sqlite_vec, semantic_text_splitter, mcp
import fastapi, uvicorn, watchdog, httpx'

# Importing sqlite_vec is NOT the same as being able to load it. A Python
# built without loadable SQLite extensions imports the package happily and
# then cannot open a single bank -- the failure surfaces at the first search,
# as an AttributeError from inside the store. So ask the question that
# actually matters, here, where the answer is still cheap to act on.
VEC_PROBE='import sqlite3, sqlite_vec
conn = sqlite3.connect(":memory:")
conn.enable_load_extension(True)
sqlite_vec.load(conn)'

# report <test-flag> <path> <label> — kept inside `if` so a failing
# test never trips `set -e`.
report() {
	if [ "$1" "$2" ]; then line "$3" present; else line "$3" MISSING; fi
}

# PID of a live service, empty when none. service.pid is written by
# service_ctl, service.json by the backend — either identifies the process
# that would hold the venv open during a refresh.
live_service_pid() {
	for name in service.pid service.json; do
		file="$MNEMO_HOME/state/$name"
		[ -f "$file" ] || continue
		candidate="$(sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "$file" | head -n1)"
		[ -n "$candidate" ] || continue
		if kill -0 "$candidate" 2>/dev/null; then
			printf '%s' "$candidate"
			return 0
		fi
	done
	printf ''
}

service_endpoint() {
	file="$MNEMO_HOME/state/service.json"
	[ -f "$file" ] || { printf ''; return 0; }
	host="$(sed -n 's/.*"host"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$file" | head -n1)"
	port="$(sed -n 's/.*"port"[[:space:]]*:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' "$file" | head -n1)"
	if [ -n "$host" ] && [ -n "$port" ]; then
		printf 'http://%s:%s' "$host" "$port"
	else
		printf ''
	fi
}

# --- --check: report only, mutate nothing ------------------------------
if [ "$CHECK_ONLY" -eq 1 ]; then
	say "engine home: $MNEMO_HOME"
	report -d "$MNEMO_HOME"                  "home dir"
	report -f "$CURRENT_LINK/src/cli.py"     "engine code"
	report -x "$PY_BIN"                      "venv python"
	report -x "$LAUNCHER"                    "launcher"
	if [ -x "$PY_BIN" ] \
		&& "$PY_BIN" -c "$DEP_PROBE" 2>/dev/null; then
		line "python deps" present
	else
		line "python deps" "MISSING / incomplete"
	fi
	if [ -x "$PY_BIN" ] \
		&& "$PY_BIN" -c "$VEC_PROBE" 2>/dev/null; then
		line "sqlite-vec" loadable
	else
		line "sqlite-vec" "UNAVAILABLE (this Python cannot load extensions)"
	fi
	if [ -d "$MNEMO_HOME/model-cache" ] \
		&& find "$MNEMO_HOME/model-cache" -mindepth 1 -print -quit 2>/dev/null | grep -q .; then
		line "model cache" "present (warmed)"
	else
		line "model cache" "empty (run: mnemo warmup)"
	fi

	# v3 state: the service, its endpoint, the token and the bank registry.
	# banks.json is NOT reconstructible from the .md, so it is reported
	# explicitly rather than assumed.
	service_pid="$(live_service_pid)"
	if [ -n "$service_pid" ]; then
		line "service" "running (pid $service_pid)"
	else
		line "service" stopped
	fi

	endpoint="$(service_endpoint)"
	line "endpoint" "${endpoint:-not published}"

	if [ -n "$service_pid" ] && [ -n "$endpoint" ] \
		&& command -v curl >/dev/null 2>&1; then
		if curl -fsS --max-time 3 "$endpoint/health" >/dev/null 2>&1; then
			line "health" "ok (200)"
		else
			line "health" "UNREACHABLE (process alive, /health silent)"
		fi
	else
		line "health" "not checked (service down)"
	fi

	# /api is open by default with none of this (design decision #34) --
	# /mcp-admin and /mcp-tools are the only things that ever mint
	# state/api.token, and only lazily, on their own first use. Absent
	# here is the normal steady state, not something to fix.
	if [ -n "${MNEMO_API_TOKEN:-}" ]; then
		line "api token" "set (MNEMO_API_TOKEN) -- /api requires it"
	elif [ -f "$MNEMO_HOME/state/api.token" ]; then
		line "api token" "set (state file, minted for /mcp-admin or /mcp-tools) -- /api requires it"
	else
		line "api token" "not set -- /api is open on loopback by default"
	fi

	if [ -f "$MNEMO_HOME/state/banks.json" ]; then
		if count="$("$PY_BIN" -c 'import json,sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
banks = data.get("banks", data) if isinstance(data, dict) else data
print(len(banks))' "$MNEMO_HOME/state/banks.json" 2>/dev/null)"; then
			line "banks" "$count registered"
		else
			line "banks" "PRESENT BUT UNPARSEABLE — do not delete, fix by hand"
		fi
	else
		line "banks" "none registered"
	fi

	if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/mnemo.service" ]; then
		line "autostart" "enabled (systemd --user)"
	else
		line "autostart" disabled
	fi
	exit 0
fi

# --- --deps-only: refresh the CURRENT version's venv packages only ------
# Safe to run while the repo's src/ is mid-refactor: engine code is left
# exactly as it is, same as install.ps1's -DepsOnly. There is no launcher
# metadata to refresh here the way install.ps1 refreshes its pip-generated
# exe's declared dependencies -- bin/mnemo is a plain script with nothing
# baked into it.
if [ "$DEPS_ONLY" -eq 1 ]; then
	[ -x "$PY_BIN" ] \
		|| { echo "install.sh: --deps-only needs an installed engine at $MNEMO_HOME" >&2; exit 1; }
	[ -f "$SRC_REPO/requirements.txt" ] \
		|| { echo "install.sh: run from the mnemo repo (requirements.txt not found)" >&2; exit 1; }
	say "engine home: $MNEMO_HOME"
	cp "$SRC_REPO/requirements.txt" "$CURRENT_LINK/requirements.txt"
	"$PY_BIN" -m pip install --quiet --upgrade pip
	run_with_heartbeat "installing python dependencies" "$PY_BIN" -m pip install --quiet -r "$CURRENT_LINK/requirements.txt"
	say "python deps installed (deps-only: engine code untouched)"
	exit 0
fi

# --- preflight ---------------------------------------------------------
command -v python3 >/dev/null 2>&1 \
	|| { echo "install.sh: python3 not found" >&2; exit 1; }
if [ "$(python3 -c 'import sys; print(1 if sys.version_info[:2] >= (3, 10) else 0)')" != 1 ]; then
	echo "install.sh: Python >= 3.10 is required" >&2
	exit 1
fi
[ -f "$SRC_REPO/src/cli.py" ] \
	|| { echo "install.sh: run from the mnemo repo (src/cli.py not found)" >&2; exit 1; }

# --- 1. layout (state/ and model-cache/ are never deleted) -------------
mkdir -p \
	"$MNEMO_HOME/state" \
	"$MNEMO_HOME/model-cache" \
	"$MNEMO_HOME/bin" \
	"$VERSIONS_DIR"
say "engine home: $MNEMO_HOME"

# A stub only -- created once, never overwritten, so a machine's own
# overrides already written here survive every later install/update.
if [ ! -f "$MNEMO_HOME/state/mnemo.env" ]; then
	cat > "$MNEMO_HOME/state/mnemo.env" <<-'EOF'
	# mnemo machine-config overrides (read by config.py on every process start)
	#
	# KEY=value here overrides that variable's default in config.py, and
	# survives every future engine update -- this file lives under state/,
	# which install.sh never touches. See docs/Memory-contracts-v3.md,
	# section 11.1, for the full list of variables.
	#
	# Example:
	# MNEMO_EMBED_IDLE_TIMEOUT=0
	EOF
	say "mnemo.env stub created (state/mnemo.env)"
fi

# --- 1a. is this a v2 engine? ------------------------------------------
# Recognised by what v2 never had: a banks registry. v2 indexed one project
# per invocation and named the file sha1(PROJECT root); v3 registers banks
# and names it sha1(BANK root), so every v2 index is orphaned the moment v3
# runs -- never opened again, and carrying no `meta` table to say whose it
# was. Absent banks.json plus index files is therefore unambiguous, and safe:
# with no registry, no index can belong to a live bank.
#
# Measured before the build, since the build is what makes this v3. Acted
# on after the launcher exists.
LEGACY_INDEXES=0
LEGACY_BYTES=0
if [ ! -f "$MNEMO_HOME/state/banks.json" ]; then
	for db in "$MNEMO_HOME"/state/*.db; do
		[ -f "$db" ] || continue
		case "$(basename "$db")" in service.db) continue ;; esac
		LEGACY_INDEXES=$((LEGACY_INDEXES + 1))
		LEGACY_BYTES=$((LEGACY_BYTES + $(file_size "$db")))
	done
fi
if [ "$LEGACY_INDEXES" -gt 0 ]; then
	say "found a v2 engine: $LEGACY_INDEXES index file(s), $(human "$LEGACY_BYTES")"
fi

# --- 2. stop -> refresh -> start ---------------------------------------
# A running backend holds the (current) venv open; it must be down before
# the build, and it comes back only if it was up to begin with. Dispatches
# through the launcher, which resolves `current` fresh -- correct whether
# `current` exists yet (a real refresh) or not (nothing was running, this
# whole block is skipped).
SERVICE_WAS_RUNNING=0
service_pid="$(live_service_pid)"
if [ -n "$service_pid" ]; then
	SERVICE_WAS_RUNNING=1
	[ -x "$LAUNCHER" ] && "$LAUNCHER" service stop >/dev/null 2>&1 || true
	for _ in $(seq 20); do
		kill -0 "$service_pid" 2>/dev/null || break
		sleep 0.1
	done
	if kill -0 "$service_pid" 2>/dev/null; then
		kill -9 "$service_pid" 2>/dev/null || true
		say "service force-stopped for refresh (pid $service_pid)"
	else
		say "service stopped for refresh (pid $service_pid)"
	fi
fi

# --- 3. build this version: code mirror + venv + deps -------------------
# Was Sync-EngineCode + the flat MNEMO_HOME/.venv build; now targets
# VERSION_DIR (versions/local/) instead of MNEMO_HOME directly -- the same
# split install.ps1's Build-EngineVersion makes.
mkdir -p "$VERSION_DIR"
if command -v rsync >/dev/null 2>&1; then
	rsync -a --delete --exclude='__pycache__' "$SRC_REPO/src/" "$VERSION_DIR/src/"
else
	rm -rf "$VERSION_DIR/src"
	mkdir -p "$VERSION_DIR/src"
	cp -R "$SRC_REPO/src/." "$VERSION_DIR/src/"
	find "$VERSION_DIR/src" -name __pycache__ -type d -prune -exec rm -rf {} +
fi
cp "$SRC_REPO/requirements.txt" "$VERSION_DIR/requirements.txt"
say "engine code refreshed ($VERSION_DIR)"

VERSION_PY_BIN="$VERSION_DIR/.venv/bin/python"
if [ ! -x "$VERSION_PY_BIN" ]; then
	python3 -m venv "$VERSION_DIR/.venv"
	say "virtualenv created"
else
	say "virtualenv reused"
fi

"$VERSION_PY_BIN" -m pip install --quiet --upgrade pip
run_with_heartbeat "installing python dependencies" "$VERSION_PY_BIN" -m pip install --quiet -r "$VERSION_DIR/requirements.txt"
say "python deps installed"

# Fail here rather than at the user's first search. Everything below this
# line builds an engine that could not open a bank.
if ! "$VERSION_PY_BIN" -c "$VEC_PROBE" 2>/dev/null; then
	printf 'install.sh: ERROR: this Python cannot load SQLite extensions, so\n' >&2
	printf '            sqlite-vec cannot load and no bank can be opened:\n' >&2
	printf '              %s\n' "$VERSION_PY_BIN" >&2
	printf '            The venv is built from whichever python3 is on PATH,\n' >&2
	printf '            so put one that has them in front of it. On macOS:\n' >&2
	printf '              brew install python@3.12\n' >&2
	printf '              PATH="$(brew --prefix python@3.12)/libexec/bin:$PATH" \\\n' >&2
	printf '                ./install.sh\n' >&2
	printf '            (Homebrew python has loadable extensions; the\n' >&2
	printf '            python.org build does not.)\n' >&2
	exit 1
fi

# --- 4. current -> VERSION_DIR (repoint) --------------------------------
# Same "atomic-ish" standard as install.ps1's Set-CurrentVersion -- neither
# side claims full transactional atomicity (Windows has no atomic directory
# rename over an existing target either). `-f` replaces an existing
# `current`; `-n` (portable: a documented option of both GNU coreutils and
# BSD/macOS `ln`) is the part that actually matters -- without it, `ln -s`
# treats an EXISTING `current` that is itself a symlink-to-a-directory as
# "put the new link inside it" rather than "replace it", which is exactly
# the wrong behaviour here (the same class of mistake `_remove_link()` in
# service_ctl.py exists to avoid on the Windows/junction side).
ln -sfn "$VERSION_DIR" "$CURRENT_LINK"
say "current -> $VERSION_TAG"

# --- 5. launcher: self-locating, no hardcoded home ---------------------
#
# Two different things are both called "engine home" and must NOT be
# conflated: MNEMO_HOME stays the UNVERSIONED root (state/ and
# model-cache/ are shared across every version, never duplicated), while
# the interpreter and PYTHONPATH/sys.path must point INSIDE `current` --
# that is where src/ and .venv actually live now. This script itself is
# plain text and resolves both fresh from BASH_SOURCE on every invocation
# -- no "frozen at build time" problem here, and no subprocess-dispatch
# trick is needed: `exec` replaces this shell with python directly, still
# one process.
cat > "$LAUNCHER" <<'LAUNCHER_EOF'
#!/usr/bin/env bash
# mnemo launcher (written by install.sh). Resolves its own engine home
# from its location, so the same file is correct on any machine / user.
# Not for humans: called only by git-tracked hooks, the MCP registration
# and the mnemo-adopt skill.
set -euo pipefail
HOME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_DIR="$HOME_DIR/current"
exec env PYTHONPATH="$CURRENT_DIR" MNEMO_HOME="$HOME_DIR" \
	"$CURRENT_DIR/.venv/bin/python" \
	-c 'import os,sys; sys.path.insert(0, os.environ["PYTHONPATH"]); from src.cli import main; raise SystemExit(main())' \
	"$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
say "launcher written: $LAUNCHER"

# No Publish-Launchers equivalent needed here, or on any future self-update
# switch: bin/mnemo (above) is a plain resolving script, never a baked
# binary, so there is nothing for retention to eventually break the way
# Windows' bin\mnemo.exe can (self-update step 12, bug A) -- it is correct
# forever, unconditionally, the moment it is written once.

# --- 5a. retire the v2 indexes -----------------------------------------
# Not a user-scope action -- it only touches the state directory of the home
# being installed -- so it runs for a custom --home too. `clean-orphans`
# rather than an `rm` loop: it is the tested path, it refuses service.db, and
# it re-reads the registry at the moment of deletion instead of trusting a
# listing taken earlier.
if [ "$LEGACY_INDEXES" -gt 0 ]; then
	say "v2 keyed indexes by project root, v3 keys them by bank root:"
	say "  none of them can be reused, and nothing else will ever open them."
	"$LAUNCHER" clean-orphans --yes \
		|| say "could not remove the v2 indexes; run '$LAUNCHER clean-orphans' by hand"
	say "v2 registered no banks, so every project needs 'mnemo init' again."
	say "the check below lists the ones this machine can still find."
fi

# --- 6. shell profile: export the token, register `mnemo` --------------
# User-scope registrations belong to the real engine only: a custom --home is
# an isolated/manual copy (and the test suite uses one), so it must never
# reach out and edit the user's shell profile or systemd units.
# It also gets no 2.2 GB download, no service claiming the port and no
# verification of something that is not the machine's engine — it only
# restores a service this same run stopped.
if [ "$MNEMO_HOME" != "$DEFAULT_HOME" ]; then
	say "isolated home: skipped token export, profile and autostart"
	if [ "$SERVICE_WAS_RUNNING" -eq 1 ]; then
		"$LAUNCHER" service start || \
			say "the service did not come back up; start it with: $LAUNCHER service start"
	fi
	say "isolated home: skipped the model, the service and the check"
	say "done."
	exit 0
fi

# A fenced block, so a re-run rewrites exactly this and nothing else. No
# PATH mutation: `mnemo` becomes a function pointing at the full path.
PROFILE_FILE="$HOME/.profile"
[ -f "$PROFILE_FILE" ] || PROFILE_FILE="$HOME/.bashrc"
BEGIN_MARK="# >>> mnemo >>>"
END_MARK="# <<< mnemo <<<"

# No MNEMO_API_TOKEN export here any more (2026-08-21, design decision #34,
# Memory-design-v3.md §13): /api no longer requires a token by default, so
# minting and exporting one on every install was exactly the auto-
# provisioning that decision removes. Its own old rationale ("the
# git-tracked .mcp.json refers to ${MNEMO_API_TOKEN}") was already stale —
# the real template (.mcp.json.template) addresses a BANK token via
# {{MNEMO_TOKEN}}, not this one. Setting $MNEMO_API_TOKEN by hand still
# gates /api exactly as before; this only stops the installer from doing it
# unasked.
{
	printf '%s\n' "$BEGIN_MARK"
	printf '# Added by install.sh — no PATH mutation, full paths only.\n'
	printf 'mnemo() { "%s" "$@"; }\n' "$LAUNCHER"
	printf '%s\n' "$END_MARK"
} > "$MNEMO_HOME/state/.profile-block"

touch "$PROFILE_FILE"
if grep -qF "$BEGIN_MARK" "$PROFILE_FILE" 2>/dev/null; then
	# Replace the fenced block in place, leave everything else untouched.
	awk -v begin="$BEGIN_MARK" -v end="$END_MARK" -v block="$MNEMO_HOME/state/.profile-block" '
		$0 == begin { skip = 1; while ((getline line < block) > 0) print line; close(block); next }
		$0 == end { skip = 0; next }
		!skip { print }
	' "$PROFILE_FILE" > "$PROFILE_FILE.mnemo-tmp"
	mv "$PROFILE_FILE.mnemo-tmp" "$PROFILE_FILE"
	say "shell profile: mnemo block refreshed ($PROFILE_FILE)"
else
	printf '\n' >> "$PROFILE_FILE"
	cat "$MNEMO_HOME/state/.profile-block" >> "$PROFILE_FILE"
	say "shell profile: mnemo registered ($PROFILE_FILE)"
fi
rm -f "$MNEMO_HOME/state/.profile-block"

# --- 7. autostart (systemd --user + linger) ----------------------------
if [ "$NO_AUTOSTART" -eq 0 ]; then
	"$LAUNCHER" autostart enable || \
		say "autostart registration failed (run: $LAUNCHER autostart enable)"
fi

# --- 8. the model ------------------------------------------------------
# Asks the engine whether the cache is complete: a half-downloaded snapshot
# must read as absent, or the installer skips the warmup that repairs it.
model_cached() {
	MNEMO_HOME="$MNEMO_HOME" "$PY_BIN" -c '
import os, sys
home = sys.argv[1]
src = sys.argv[2]
os.environ["MNEMO_HOME"] = home
sys.path.insert(0, src)
from src.embedder import is_model_cached
raise SystemExit(0 if is_model_cached() else 1)
' "$MNEMO_HOME" "$CURRENT_LINK" 2>/dev/null
}

want_model() {
	[ "$NO_MODEL" -eq 1 ] && return 1
	[ "$WANT_MODEL" -eq 1 ] && return 0
	# Nobody is there to answer in CI, a unit or a piped run, and a prompt
	# nobody sees would either hang or read the caller's data as the answer.
	if [ ! -t 0 ]; then
		say "the embedding model is not cached; skipping the download (non-interactive run — pass --model to fetch it)"
		return 1
	fi
	say "the embedding model is not cached yet (~2.2 GB, one time)."
	printf 'install.sh: download it now? [Y/n] '
	read -r reply
	case "$reply" in
		""|y|Y|yes|YES) return 0 ;;
		*) return 1 ;;
	esac
}

if model_cached; then
	say "model already cached"
elif want_model; then
	run_with_heartbeat "downloading the embedding model (~2.2 GB, one time)" "$LAUNCHER" warmup \
		|| say "the model download failed; retry with: $LAUNCHER warmup"
else
	say "skipped the model. Search will not work until you run:"
	say "  $LAUNCHER warmup"
fi

# --- 9. start the service ----------------------------------------------
# Started even when nothing was running before — that is the whole point on
# a clean machine. Registering a logon unit and then leaving the service down
# until the next reboot is the kind of half-install that reads as "broken".
if [ "$NO_START" -eq 0 ]; then
	"$LAUNCHER" service start || \
		say "the service did not start; try: $LAUNCHER service start"
fi

# --- 10. end on evidence, not on a promise -----------------------------
say "verifying --"
"$LAUNCHER" doctor || true

say "done."
# A new terminal, not this one: the profile function just registered above
# only loads when a shell starts, so THIS shell still needs the full path --
# the fallback below.
say_hl "Open a new terminal -- \"mnemo init\" will already work there."
say "  (or right now, in this shell: cd <your project> && $LAUNCHER init)"
