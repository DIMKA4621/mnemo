#!/usr/bin/env bash
# mnemo engine uninstaller — system scope, the exact inverse of install.sh.
#
# It removes everything the installer writes to this machine, and nothing
# else. Your projects, their `.md` and this repository are never touched —
# the markdown is the source of truth, so what goes here is only the
# derived, rebuildable half.
#
#   ./uninstall.sh              remove it all (shows the list, asks first)
#   ./uninstall.sh --dry-run    show what would go, change nothing
#   ./uninstall.sh --yes        do not ask (scripts)
#   ./uninstall.sh --keep-model leave model-cache/ (~2.2 GB) in place
#   ./uninstall.sh --keep-state leave state/ (registry, indexes, journal)
#   ./uninstall.sh --home DIR   uninstall an isolated copy instead
#
# An uninstaller is reached for precisely when something is already broken,
# so no step depends on another having worked: every removal is independent
# and best-effort, and a missing unit, launcher or venv is a state to report
# rather than a reason to abort. Exit code 1 only if something that exists
# could not be removed.
#
# Default location: $HOME/.claude/mnemo  (override with $MNEMO_HOME).
set -uo pipefail

usage() {
	awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' \
		"${BASH_SOURCE[0]}"
}

say() { printf 'uninstall.sh: %s\n' "$1"; }
line() { printf 'uninstall.sh:   %-15s %s\n' "$1" "$2"; }

# --- flags -------------------------------------------------------------
DEFAULT_HOME="$HOME/.claude/mnemo"
MNEMO_HOME="${MNEMO_HOME:-$DEFAULT_HOME}"
KEEP_MODEL=0
KEEP_STATE=0
DRY_RUN=0
ASSUME_YES=0
while [ $# -gt 0 ]; do
	case "$1" in
		--keep-model) KEEP_MODEL=1 ;;
		--keep-state) KEEP_STATE=1 ;;
		--dry-run) DRY_RUN=1 ;;
		-y|--yes) ASSUME_YES=1 ;;
		--home) shift; MNEMO_HOME="${1:?--home needs a directory}" ;;
		--home=*) MNEMO_HOME="${1#--home=}" ;;
		-h|--help) usage; exit 0 ;;
		*) echo "uninstall.sh: unknown argument: $1" >&2; exit 2 ;;
	esac
	shift
done

LAUNCHER="$MNEMO_HOME/bin/mnemo"
STATE_DIR="$MNEMO_HOME/state"
MODEL_DIR="$MNEMO_HOME/model-cache"
VENV_DIR="$MNEMO_HOME/.venv"
SYSTEMD_UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/mnemo.service"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/dev.mnemo.service.plist"
BEGIN_MARK="# >>> mnemo >>>"
END_MARK="# <<< mnemo <<<"

FAILURES=0
result() {
	# result <item> <status> [detail]
	if [ -n "${3:-}" ]; then line "$1" "$2 — $3"; else line "$1" "$2"; fi
	[ "$2" = failed ] && FAILURES=$((FAILURES + 1))
	return 0
}

# --- survey helpers ----------------------------------------------------
human() {
	# Bytes → a size a person reads. No `numfmt`: it is GNU-only and this
	# has to work on macOS too.
	awk -v b="${1:-0}" 'BEGIN {
		split("B KiB MiB GiB TiB", u, " ")
		i = 1
		while (b >= 1024 && i < 5) { b /= 1024; i++ }
		if (i == 1) printf "%d %s\n", b, u[i]
		else printf "%.1f %s\n", b, u[i]
	}'
}

tree_size() {
	# Apparent size in bytes, portable between GNU and BSD `du`.
	[ -e "$1" ] || { echo 0; return 0; }
	if du -sb "$1" >/dev/null 2>&1; then
		du -sb "$1" 2>/dev/null | awk '{print $1}'
	else
		du -sk "$1" 2>/dev/null | awk '{print $1 * 1024}'
	fi
}

# The PIDs mnemo itself recorded — not processes found by scanning. Both
# files matter and may disagree by design: service.pid is what `service
# start` spawned, service.json is what the backend published about itself.
recorded_pids() {
	local name pid
	for name in service.pid service.json; do
		[ -f "$STATE_DIR/$name" ] || continue
		pid="$(python3 - "$STATE_DIR/$name" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(int(json.load(handle).get("pid") or 0))
except Exception:
    print(0)
PY
)"
		[ -n "$pid" ] && [ "$pid" -gt 0 ] 2>/dev/null || continue
		kill -0 "$pid" 2>/dev/null && echo "$pid"
	done | sort -u
}

registered_banks() {
	[ -f "$STATE_DIR/banks.json" ] || return 0
	python3 - "$STATE_DIR/banks.json" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    raise SystemExit(0)
banks = data.get("banks", data) if isinstance(data, dict) else data
for bank in banks or []:
    if isinstance(bank, dict):
        print("%s\t%s" % (bank.get("name", "(unnamed)"), bank.get("root", "(no root)")))
PY
}

autostart_present() {
	[ -f "$SYSTEMD_UNIT" ] || [ -f "$LAUNCHD_PLIST" ]
}

profile_file() {
	# The same file install.sh wrote to, chosen the same way.
	if [ -f "$HOME/.profile" ]; then echo "$HOME/.profile"; else echo "$HOME/.bashrc"; fi
}

profile_has_block() {
	local file
	file="$(profile_file)"
	[ -f "$file" ] && grep -qF "$BEGIN_MARK" "$file" 2>/dev/null
}

# --- survey: measure before touching anything --------------------------
say "engine home: $MNEMO_HOME"
[ -d "$MNEMO_HOME" ] || say "no engine installed there (machine-level registrations are still checked)."

TOTAL_SIZE="$(tree_size "$MNEMO_HOME")"
STATE_SIZE="$(tree_size "$STATE_DIR")"
MODEL_SIZE="$(tree_size "$MODEL_DIR")"
VENV_SIZE="$(tree_size "$VENV_DIR")"
RUNNING="$(recorded_pids | tr '\n' ' ' | sed 's/ $//')"
INDEX_COUNT="$(find "$STATE_DIR" -maxdepth 1 -name '*.db' -type f 2>/dev/null | wc -l | tr -d ' ')"
PROFILE_FILE="$(profile_file)"
IS_DEFAULT_HOME=0
[ "$MNEMO_HOME" = "$DEFAULT_HOME" ] && IS_DEFAULT_HOME=1

GOING_SIZE="$TOTAL_SIZE"
[ "$KEEP_MODEL" -eq 1 ] && GOING_SIZE=$((GOING_SIZE - MODEL_SIZE))
[ "$KEEP_STATE" -eq 1 ] && GOING_SIZE=$((GOING_SIZE - STATE_SIZE))

say "this would remove --"
if [ -f "$MNEMO_HOME/src/cli.py" ]; then
	line "engine code" "src/, launcher, requirements"
else
	line "engine code" MISSING
fi
line "virtualenv" "$([ "$VENV_SIZE" -gt 0 ] && human "$VENV_SIZE" || echo absent)"
if [ "$KEEP_MODEL" -eq 1 ]; then
	line "model cache" "KEPT ($(human "$MODEL_SIZE"))"
else
	line "model cache" "$([ "$MODEL_SIZE" -gt 0 ] && echo "$(human "$MODEL_SIZE") — re-download on next install" || echo absent)"
fi
if [ "$KEEP_STATE" -eq 1 ]; then
	line "state" "KEPT ($(human "$STATE_SIZE"), $INDEX_COUNT index files)"
else
	line "state" "$([ "$STATE_SIZE" -gt 0 ] && echo "$(human "$STATE_SIZE"), $INDEX_COUNT index files, registry, journal, tokens" || echo absent)"
fi
line "service" "$([ -n "$RUNNING" ] && echo "running (pid $RUNNING) — will be stopped" || echo "not running")"
# The autostart unit and the profile block are machine-level and belong to the
# real engine. Listing them under "this would remove" for an isolated copy
# would be a promise the removal step correctly refuses to keep.
if [ "$IS_DEFAULT_HOME" -eq 1 ]; then
	if [ -f "$SYSTEMD_UNIT" ]; then
		line "autostart" "systemd --user unit ($SYSTEMD_UNIT)"
	elif [ -f "$LAUNCHD_PLIST" ]; then
		line "autostart" "launchd agent ($LAUNCHD_PLIST)"
	else
		line "autostart" "not registered"
	fi
	line "profile block" "$(profile_has_block && echo "$PROFILE_FILE" || echo "not present")"
fi
line "total" "$(human "$GOING_SIZE")"

[ "$IS_DEFAULT_HOME" -eq 1 ] || \
	say "isolated home: the profile block and the autostart unit belong to the real engine and are left alone"

BANKS="$(registered_banks)"
if [ -n "$BANKS" ] && [ "$KEEP_STATE" -eq 0 ]; then
	say "$(printf '%s\n' "$BANKS" | wc -l | tr -d ' ') registered bank(s) will be forgotten. Their .md are NOT touched:"
	printf '%s\n' "$BANKS" | while IFS="$(printf '\t')" read -r name root; do
		line "$name" "$root"
	done
	say "after reinstalling, re-run 'mnemo init' in each project: the tokens in their .mcp.json refer to a registry that will no longer exist."
fi

if [ "$DRY_RUN" -eq 1 ]; then
	say "dry run: nothing was removed."
	exit 0
fi

# --- confirm -----------------------------------------------------------
if [ "$ASSUME_YES" -eq 0 ]; then
	if [ ! -t 0 ]; then
		# A prompt nobody can see would hang forever or read one byte of the
		# caller's data as consent — for a delete, neither is acceptable.
		say "non-interactive run and no --yes: nothing was removed."
		say "re-run with --yes to confirm, or --dry-run to see the list."
		exit 0
	fi
	say "about to remove $(human "$GOING_SIZE"). This cannot be undone."
	printf 'uninstall.sh: proceed? [y/N] '
	read -r reply
	case "$reply" in
		y|Y|yes|YES) ;;
		*) say "cancelled; nothing was removed."; exit 0 ;;
	esac
fi

# --- removal: independent steps, none aborts the rest ------------------
if [ -n "$RUNNING" ]; then
	# The launcher first, always: `service stop` knows the fingerprint check,
	# refuses to kill a PID it does not own, and takes the embedding resident
	# down with it (which is what releases the model files). Signalling the
	# recorded PIDs is the fallback for an engine too broken to have one.
	[ -x "$LAUNCHER" ] && "$LAUNCHER" service stop >/dev/null 2>&1
	for _ in $(seq 30); do
		[ -z "$(recorded_pids)" ] && break
		sleep 0.1
	done
	if [ -n "$(recorded_pids)" ]; then
		for pid in $(recorded_pids); do kill -TERM "$pid" 2>/dev/null; done
		sleep 1
		for pid in $(recorded_pids); do kill -KILL "$pid" 2>/dev/null; done
		sleep 0.3
	fi
	if [ -z "$(recorded_pids)" ]; then
		result service removed stopped
	else
		result service failed "still running (pid $(recorded_pids | tr '\n' ' ')) — stop it and re-run"
	fi
else
	result service absent "not running"
fi

if [ "$IS_DEFAULT_HOME" -eq 1 ]; then
	if autostart_present; then
		# `autostart disable` owns the details (it also leaves `loginctl
		# enable-linger` alone on purpose — the user may want it for their
		# own units). Only fall back when the launcher is gone.
		[ -x "$LAUNCHER" ] && "$LAUNCHER" autostart disable >/dev/null 2>&1
		if autostart_present; then
			if [ -f "$SYSTEMD_UNIT" ]; then
				systemctl --user disable --now mnemo.service >/dev/null 2>&1
				rm -f "$SYSTEMD_UNIT"
				systemctl --user daemon-reload >/dev/null 2>&1
			fi
			if [ -f "$LAUNCHD_PLIST" ]; then
				launchctl unload "$LAUNCHD_PLIST" >/dev/null 2>&1
				rm -f "$LAUNCHD_PLIST"
			fi
		fi
		if autostart_present; then
			result autostart failed "remove it by hand"
		else
			result autostart removed "lingering left as it was"
		fi
	else
		result autostart absent
	fi

	if profile_has_block; then
		# Only mnemo's fence goes — and the export of MNEMO_API_TOKEN lives
		# inside it, so the variable leaves with the block. Everything else
		# in the profile stays byte-for-byte.
		if awk -v begin="$BEGIN_MARK" -v end="$END_MARK" '
			$0 == begin { skip = 1; next }
			$0 == end { skip = 0; next }
			!skip { print }
		' "$PROFILE_FILE" > "$PROFILE_FILE.mnemo-tmp" && mv "$PROFILE_FILE.mnemo-tmp" "$PROFILE_FILE"; then
			result "profile block" removed "$PROFILE_FILE"
		else
			rm -f "$PROFILE_FILE.mnemo-tmp"
			result "profile block" failed "$PROFILE_FILE"
		fi
	else
		result "profile block" absent
	fi
fi

remove_tree() {
	# remove_tree <path> <label>
	local size
	[ -e "$1" ] || { result "$2" absent; return 0; }
	size="$(tree_size "$1")"
	if rm -rf "$1" 2>/dev/null && [ ! -e "$1" ]; then
		result "$2" removed "$(human "$size")"
	else
		result "$2" failed "$1"
	fi
}

if [ "$KEEP_MODEL" -eq 1 ] || [ "$KEEP_STATE" -eq 1 ]; then
	# Selective: remove every child of the engine home except the kept ones.
	# Children rather than a whitelist, so leftovers of older layouts go too.
	if [ -d "$MNEMO_HOME" ]; then
		for child in "$MNEMO_HOME"/* "$MNEMO_HOME"/.[!.]*; do
			[ -e "$child" ] || continue
			base="$(basename "$child")"
			if { [ "$KEEP_MODEL" -eq 1 ] && [ "$base" = "model-cache" ]; } || \
			   { [ "$KEEP_STATE" -eq 1 ] && [ "$base" = "state" ]; }; then
				result "$base" kept "$(human "$(tree_size "$child")")"
				continue
			fi
			remove_tree "$child" "$base"
		done
	else
		result "engine home" absent
	fi
else
	remove_tree "$MNEMO_HOME" "engine home"
fi

# --- what deliberately survives ----------------------------------------
say "left alone on purpose --"
line "your projects" ".md, .mcp.json, .claude/rules/mnemo-memory.md — the markdown is the source of truth"
line "this repository" "the source; delete the checkout by hand if you want it gone"
[ -f "$SYSTEMD_UNIT" ] || line "lingering" "loginctl enable-linger is left as it was — it may serve your own units"

if [ "$FAILURES" -gt 0 ]; then
	say "done, with $FAILURES item(s) NOT removed (listed above)."
	exit 1
fi
say "done. mnemo is gone from this machine."
