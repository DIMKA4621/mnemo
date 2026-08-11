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
# Default location: $HOME/.claude/mnemo  (override with $MNEMO_HOME).
set -euo pipefail

usage() {
	# Prints the header block above, however long it grows. It used to be a
	# fixed line range, which silently truncated the moment a flag was added.
	awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' \
		"${BASH_SOURCE[0]}"
}

say() { printf 'install.sh: %s\n' "$1"; }

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

# --- resolve the engine home and parse flags ---------------------------
DEFAULT_HOME="$HOME/.claude/mnemo"
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

PY_BIN="$MNEMO_HOME/.venv/bin/python"
LAUNCHER="$MNEMO_HOME/bin/mnemo"

line() { printf 'install.sh:   %-13s %s\n' "$1" "$2"; }

# Every runtime dependency the engine imports (v2 core + v3 service layer).
DEP_PROBE='import fastembed, sqlite_vec, semantic_text_splitter, mcp
import fastapi, uvicorn, watchdog, httpx'

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
	report -d "$MNEMO_HOME"            "home dir"
	report -f "$MNEMO_HOME/src/cli.py" "engine code"
	report -x "$PY_BIN"               "venv python"
	report -x "$LAUNCHER"             "launcher"
	if [ -x "$PY_BIN" ] \
		&& "$PY_BIN" -c "$DEP_PROBE" 2>/dev/null; then
		line "python deps" present
	else
		line "python deps" "MISSING / incomplete"
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

	if [ -f "$MNEMO_HOME/state/api.token" ]; then
		line "api token" present
	else
		line "api token" "missing (created on first service start)"
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

# --- --deps-only: refresh venv packages, leave src/ and the launcher ----
# Safe to run while the repo's src/ is mid-refactor.
if [ "$DEPS_ONLY" -eq 1 ]; then
	[ -x "$PY_BIN" ] \
		|| { echo "install.sh: --deps-only needs an installed engine at $MNEMO_HOME" >&2; exit 1; }
	[ -f "$SRC_REPO/requirements.txt" ] \
		|| { echo "install.sh: run from the mnemo repo (requirements.txt not found)" >&2; exit 1; }
	say "engine home: $MNEMO_HOME"
	cp "$SRC_REPO/requirements.txt" "$MNEMO_HOME/requirements.txt"
	"$PY_BIN" -m pip install --quiet --upgrade pip
	"$PY_BIN" -m pip install --quiet -r "$MNEMO_HOME/requirements.txt"
	say "python deps installed (deps-only: engine code and launcher untouched)"
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
	"$MNEMO_HOME/src" \
	"$MNEMO_HOME/state" \
	"$MNEMO_HOME/model-cache" \
	"$MNEMO_HOME/bin"
say "engine home: $MNEMO_HOME"

# --- 1a. is this a v2 engine? ------------------------------------------
# Recognised by what v2 never had: a banks registry. v2 indexed one project
# per invocation and named the file sha1(PROJECT root); v3 registers banks
# and names it sha1(BANK root), so every v2 index is orphaned the moment v3
# runs -- never opened again, and carrying no `meta` table to say whose it
# was. Absent banks.json plus index files is therefore unambiguous, and safe:
# with no registry, no index can belong to a live bank.
#
# Measured before the mirror, since the mirror is what makes this v3. Acted
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
# A running backend holds the venv open; it must be down before the mirror,
# and it comes back only if it was up to begin with.
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

# --- 3. mirror the engine code (only ever touches src/) ----------------
if command -v rsync >/dev/null 2>&1; then
	rsync -a --delete --exclude='__pycache__' "$SRC_REPO/src/" "$MNEMO_HOME/src/"
else
	rm -rf "$MNEMO_HOME/src"
	mkdir -p "$MNEMO_HOME/src"
	cp -R "$SRC_REPO/src/." "$MNEMO_HOME/src/"
	find "$MNEMO_HOME/src" -name __pycache__ -type d -prune -exec rm -rf {} +
fi
cp "$SRC_REPO/requirements.txt" "$MNEMO_HOME/requirements.txt"
say "engine code refreshed"

# --- 3. virtualenv (created once, reused after) ------------------------
if [ ! -x "$PY_BIN" ]; then
	python3 -m venv "$MNEMO_HOME/.venv"
	say "virtualenv created"
else
	say "virtualenv reused"
fi

# --- 4. dependencies (pip is idempotent) -------------------------------
"$PY_BIN" -m pip install --quiet --upgrade pip
"$PY_BIN" -m pip install --quiet -r "$MNEMO_HOME/requirements.txt"
say "python deps installed"

# --- 5. launcher: self-locating, no hardcoded home ---------------------
cat > "$LAUNCHER" <<'LAUNCHER_EOF'
#!/usr/bin/env bash
# mnemo launcher (written by install.sh). Resolves its own engine home
# from its location, so the same file is correct on any machine / user.
# Not for humans: called only by git-tracked hooks, the MCP registration
# and the mnemo-adopt skill.
set -euo pipefail
HOME_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec env PYTHONPATH="$HOME_DIR" MNEMO_HOME="$HOME_DIR" \
	"$HOME_DIR/.venv/bin/python" \
	-c 'import os,sys; sys.path.insert(0, os.environ["MNEMO_HOME"]); from src.cli import main; raise SystemExit(main())' \
	"$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
say "launcher written: $LAUNCHER"

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

API_TOKEN="$("$PY_BIN" -c 'import os,sys
home = sys.argv[1]
os.environ["MNEMO_HOME"] = home
sys.path.insert(0, home)
from src.api import api_token
print(api_token())' "$MNEMO_HOME" 2>/dev/null || true)"

{
	printf '%s\n' "$BEGIN_MARK"
	printf '# Added by install.sh — no PATH mutation, full paths only.\n'
	printf 'mnemo() { "%s" "$@"; }\n' "$LAUNCHER"
	if [ -n "$API_TOKEN" ]; then
		# The git-tracked .mcp.json refers to ${MNEMO_API_TOKEN}; without it
		# the placeholder never expands and MCP cannot connect.
		printf 'export MNEMO_API_TOKEN="%s"\n' "$API_TOKEN"
	fi
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
os.environ["MNEMO_HOME"] = home
sys.path.insert(0, home)
from src.embedder import is_model_cached
raise SystemExit(0 if is_model_cached() else 1)
' "$MNEMO_HOME" 2>/dev/null
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
	"$LAUNCHER" warmup || say "the model download failed; retry with: $LAUNCHER warmup"
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

say "done. One thing left, and only you know where:"
say "  cd <your project> && $LAUNCHER init"
