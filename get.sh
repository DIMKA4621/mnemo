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
# pointing the bootstrapper at an unreleased branch by hand. If the
# releases/latest lookup itself fails -- no releases yet, GitHub
# unreachable -- this falls back to `master` rather than failing the whole
# install over a single API call.
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
	if RELEASE_JSON="$(curl -fsS --max-time 10 -H 'Accept: application/vnd.github+json' -H 'User-Agent: mnemo-bootstrap' "$API_URL" 2>/dev/null)"; then
		TAG="$(printf '%s' "$RELEASE_JSON" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)"
	fi
	if [ -n "$TAG" ]; then
		ARCHIVE_URL="https://codeload.github.com/${REPO}/tar.gz/refs/tags/${TAG}"
		REF_LABEL="$TAG"
	else
		echo "get.sh: no GitHub release found, falling back to master"
		ARCHIVE_URL="https://codeload.github.com/${REPO}/tar.gz/refs/heads/master"
		REF_LABEL="master"
	fi
fi

tmp="$(mktemp -d "${TMPDIR:-/tmp}/mnemo-src-XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

echo "get.sh: downloading mnemo ($REF_LABEL)..."
curl -fsSL "$ARCHIVE_URL" | tar xz -C "$tmp"

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

echo "get.sh: installing..."
bash "$extracted/install.sh" "$@"
