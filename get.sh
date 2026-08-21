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
# install.sh's flag names or need updating when those change. Its two
# internal knobs -- which branch to fetch, and a test-only archive URL
# override -- are environment variables instead: MNEMO_GET_REF (default
# "master"), MNEMO_GET_ARCHIVE_URL.

set -euo pipefail

REPO="DIMKA4621/mnemo"
REF="${MNEMO_GET_REF:-master}"
ARCHIVE_URL="${MNEMO_GET_ARCHIVE_URL:-https://codeload.github.com/${REPO}/tar.gz/refs/heads/${REF}}"

tmp="$(mktemp -d "${TMPDIR:-/tmp}/mnemo-src-XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

echo "get.sh: downloading mnemo ($REF)..."
curl -fsSL "$ARCHIVE_URL" | tar xz -C "$tmp"

# GitHub's archive always has exactly one top-level "<repo>-<ref>" dir --
# never hardcode the name, slashes in $REF get dashed by GitHub itself.
extracted="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n1)"
if [ -z "$extracted" ] || [ ! -f "$extracted/install.sh" ]; then
    echo "get.sh: extracted source is missing install.sh" >&2
    exit 1
fi

echo "get.sh: installing..."
bash "$extracted/install.sh" "$@"
