#!/usr/bin/env bash
# mnemo bootstrap installer: no local clone required.
#
#   curl -fsSL https://raw.githubusercontent.com/DIMKA4621/mnemo/master/get.sh | bash
#
# Fetches a snapshot of the repo from GitHub, extracts it to a temp
# directory, and runs the real install.sh from inside it -- unmodified,
# exactly as a manual `git clone && ./install.sh` would. The temp copy is
# always removed afterward, success or failure.
#
# Every argument is forwarded as-is to install.sh, e.g.:
#   curl -fsSL .../get.sh | bash -s -- --check --home /path
# get.sh takes no flags of its own on purpose, so it can never collide with
# install.sh's flag names or need updating when those change. Its internal
# knobs -- which ref to fetch, and a test-only archive/API URL override --
# are environment variables instead: MNEMO_GET_REF, MNEMO_GET_ARCHIVE_URL,
# MNEMO_GET_RELEASE_API_URL.
#
# Default source: the latest GitHub release, not the moving `master`
# branch -- a one-liner should hand people the last thing that was
# actually tagged and shipped, same source engine_update.py's own
# self-update pulls from, not whatever happens to be on master mid-work.
# MNEMO_GET_REF overrides this and is taken as a BRANCH name (heads/), for
# pointing the bootstrapper at an unreleased branch by hand -- an explicit,
# deliberate override, never an error path.
#
# If the releases/latest lookup itself fails -- no releases yet, GitHub
# unreachable, rate-limited -- this is a hard installation error, NOT a
# silent fallback to `master` (2026-08-22 decision, reversing the original
# soft-fallback behaviour, mirroring get.ps1): a one-liner that silently
# hands someone unreleased `master` when it meant to hand them the latest
# release would make the version they end up running depend on which
# GitHub API call happened to work that day.
#
# One default differs from install.sh's own: unless --model or --no-model
# is already among the forwarded args, get.sh adds --model itself.
# install.sh run directly still asks (or silently skips when it can't ask);
# a one-liner's whole point is a single command that finishes the job, so
# this path assumes yes instead of leaving a ~2 GB download for the user to
# trigger by hand afterward. Pass --no-model to opt out.

set -euo pipefail

REPO="DIMKA4621/mnemo"

if [ -n "${MNEMO_GET_ARCHIVE_URL:-}" ]; then
	ARCHIVE_URL="$MNEMO_GET_ARCHIVE_URL"
	REF_LABEL="custom archive"
elif [ -n "${MNEMO_GET_REF:-}" ]; then
	ARCHIVE_URL="https://codeload.github.com/${REPO}/tar.gz/refs/heads/${MNEMO_GET_REF}"
	REF_LABEL="$MNEMO_GET_REF"
else
	API_URL="${MNEMO_GET_RELEASE_API_URL:-https://api.github.com/repos/${REPO}/releases/latest}"
	TAG=""
	LOOKUP_OK=false
	if RELEASE_JSON="$(curl -fsS --max-time 10 -H 'Accept: application/vnd.github+json' -H 'User-Agent: mnemo-bootstrap' "$API_URL" 2>/dev/null)"; then
		LOOKUP_OK=true
		TAG="$(printf '%s' "$RELEASE_JSON" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)"
	fi
	if [ -n "$TAG" ]; then
		ARCHIVE_URL="https://codeload.github.com/${REPO}/tar.gz/refs/tags/${TAG}"
		REF_LABEL="$TAG"
	else
		echo "get.sh: could not find a GitHub release to install." >&2
		if [ "$LOOKUP_OK" = true ]; then
			echo "get.sh: GitHub reported no releases for ${REPO}." >&2
		else
			echo "get.sh: could not reach the GitHub API -- check your network connection." >&2
		fi
		echo "get.sh: install from a manual clone instead:" >&2
		echo "get.sh:   git clone https://github.com/${REPO}.git && cd mnemo && ./install.sh" >&2
		exit 1
	fi
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/mnemo-src-XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

# Same in-place spinner (backspace-redraw, | / - \) as install.sh's own
# run_with_heartbeat -- but this script cannot source that function yet:
# install.sh lives INSIDE the archive being downloaded here, so only the
# pattern is reused (background the command, poll its PID, redraw a
# spinner), not a shared function. Backgrounding the whole curl | tar
# pipeline (rather than just curl) is what lets the spinner keep ticking
# through the extraction too, not just the network transfer.
printf 'get.sh: downloading mnemo (%s) ' "$REF_LABEL"
(curl -fsSL "$ARCHIVE_URL" | tar xz -C "$tmp") &
dl_pid=$!
spin='|/-\'
tick=0
printed=0
while kill -0 "$dl_pid" 2>/dev/null; do
	sleep 1
	[ "$printed" -eq 1 ] && printf '\b'
	printf '%s' "${spin:$((tick % 4)):1}"
	printed=1
	tick=$((tick + 1))
done
# `wait` as a bare statement propagates a nonzero exit straight through
# `set -e`, aborting the shell right here -- before $? is ever read, so the
# "download failed" message and controlled `exit 1` below would never run,
# same bug fixed in install.sh's run_with_heartbeat (2026-08-23). The `||`
# puts `wait` in a conditional context, which `set -e` exempts.
dl_code=0
wait "$dl_pid" || dl_code=$?
# Same zero-tick guard as install.sh's run_with_heartbeat: a download that
# finishes before the loop above ever sleeps once would otherwise eat a
# character off the label instead of a spinner frame it never drew.
[ "$printed" -eq 1 ] && printf '\b'
printf 'done\n'
if [ "$dl_code" -ne 0 ]; then
	echo "get.sh: download failed" >&2
	exit 1
fi

# GitHub's archive always has exactly one top-level "<repo>-<ref>" dir --
# never hardcode the name, slashes in $REF get dashed by GitHub itself.
extracted="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n1)"
if [ -z "$extracted" ] || [ ! -f "$extracted/install.sh" ]; then
    echo "get.sh: extracted source is missing install.sh" >&2
    exit 1
fi

has_model_flag=false
for arg in "$@"; do
    case "$arg" in
        --model|--no-model) has_model_flag=true ;;
    esac
done
if [ "$has_model_flag" = false ]; then
    set -- "$@" --model
fi

# GitHub's archive carries no .git directory, so install.sh's own
# get_local_checkout_tag() can never see a tag here -- without this, every
# get.sh install would report itself as "local" forever and nag to
# "update" to the very release it just installed. get.sh already knows the
# exact tag it resolved -- pass it through rather than making install.sh
# re-derive it from nothing. Only set for a CONFIRMED release ($TAG, from
# the releases/latest branch above); stays unset for the master fallback,
# a custom archive, or an explicit MNEMO_GET_REF override -- none of those
# name an installable version, so "local" remains the correct answer.
if [ -n "${TAG:-}" ]; then
	export MNEMO_INSTALL_TAG="$TAG"
fi

echo "get.sh: installing..."
bash "$extracted/install.sh" "$@"
