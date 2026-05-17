#!/usr/bin/env bash
# mnemo engine installer — system scope, once per machine.
#
# Installs the runtime (engine code + virtualenv + launcher) under a
# single user-home directory shared by every project. Deterministic and
# idempotent: safe to re-run to refresh code and dependencies. It NEVER
# downloads the embedding model (that is the explicit `mnemo warmup`
# step) and NEVER touches the per-project index state or the model cache.
#
#   ./install.sh            install or refresh the engine
#   ./install.sh --check    report engine state, change nothing
#   ./install.sh --home DIR  install into DIR instead of the default
#
# Default location: $HOME/.claude/mnemo  (override with $MNEMO_HOME).
set -euo pipefail

usage() {
	sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^#\{0,1\} \{0,1\}//'
}

say() { printf 'install.sh: %s\n' "$1"; }

# --- locate the repo (this script's own directory) ---------------------
SRC_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- resolve the engine home and parse flags ---------------------------
MNEMO_HOME="${MNEMO_HOME:-$HOME/.claude/mnemo}"
CHECK_ONLY=0
while [ $# -gt 0 ]; do
	case "$1" in
		--check) CHECK_ONLY=1 ;;
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

# report <test-flag> <path> <label> — kept inside `if` so a failing
# test never trips `set -e`.
report() {
	if [ "$1" "$2" ]; then line "$3" present; else line "$3" MISSING; fi
}

# --- --check: report only, mutate nothing ------------------------------
if [ "$CHECK_ONLY" -eq 1 ]; then
	say "engine home: $MNEMO_HOME"
	report -d "$MNEMO_HOME"            "home dir"
	report -f "$MNEMO_HOME/src/cli.py" "engine code"
	report -x "$PY_BIN"               "venv python"
	report -x "$LAUNCHER"             "launcher"
	if [ -x "$PY_BIN" ] \
		&& "$PY_BIN" -c 'import fastembed, sqlite_vec, semantic_text_splitter, mcp' 2>/dev/null; then
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

# --- 2. mirror the engine code (only ever touches src/) ----------------
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
	"$HOME_DIR/.venv/bin/python" -m src.cli "$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
say "launcher written: $LAUNCHER"

# --- 6. done (model is intentionally NOT downloaded) -------------------
say "done. The embedding model is NOT downloaded by install."
say "warm it once with:  $LAUNCHER warmup"
