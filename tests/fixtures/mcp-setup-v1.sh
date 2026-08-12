#!/usr/bin/env bash
# Regenerate .mcp.json from .mcp.json.template + .mcp.env.
#
# mnemo:dynamic-setup/1
# Substitutions are DISCOVERED from the template, never listed here. Adding a
# server means editing the template and .mcp.env; this file never changes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/.mcp.json.template"
ENV_FILE="$SCRIPT_DIR/.mcp.env"
OUTPUT="$SCRIPT_DIR/.mcp.json"

[ -f "$TEMPLATE" ] || { echo "mcp-setup: missing $TEMPLATE" >&2; exit 1; }
if [ ! -f "$ENV_FILE" ]; then
	echo "mcp-setup: missing .mcp.env -- run: cp .mcp.env.example .mcp.env" >&2
	exit 1
fi

# Look one variable up in .mcp.env. The file is READ, never sourced: it holds
# credentials, and `source` would execute whatever a stray line happens to be.
# First definition wins, which is how the file reads top-down.
lookup() {
	local key="$1" line value
	line="$(grep -m1 -E "^[[:space:]]*${key}[[:space:]]*=" "$ENV_FILE" || true)"
	[ -n "$line" ] || return 1
	value="${line#*=}"
	# Trim, then unquote. Spaces are tolerated around the `=` on the key side,
	# so tolerating them on the value side is the only consistent reading --
	# and `PORT = 8918` otherwise yields a port with spaces in it. A value that
	# genuinely wants padding says so by quoting.
	value="${value#"${value%%[![:space:]]*}"}"
	value="${value%"${value##*[![:space:]]}"}"
	# One layer of surrounding quotes, if the value carries them.
	case "$value" in
		\"*\") value="${value#\"}"; value="${value%\"}" ;;
		\'*\') value="${value#\'}"; value="${value%\'}" ;;
	esac
	printf '%s' "$value"
}

content="$(cat "$TEMPLATE")"
missing=""

# Every placeholder the template actually contains, deduplicated. `-E` rather
# than a BRE with `\+`: BSD grep on macOS does not read that the same way.
for name in $(grep -oE '\{\{[A-Za-z0-9_]+\}\}' "$TEMPLATE" | tr -d '{}' | sort -u); do
	if value="$(lookup "$name")"; then
		# Quoted pattern, so the braces are literal and not a glob. This is
		# also why the substitution is not `sed`: a value holding the
		# delimiter would break the expression, and a token is opaque.
		content="${content//"{{$name}}"/"$value"}"
	else
		missing="$missing $name"
	fi
done

if [ -n "$missing" ]; then
	echo "mcp-setup: no value in .mcp.env for:$missing" >&2
	echo "mcp-setup: .mcp.json NOT written" >&2
	exit 1
fi

printf '%s\n' "$content" > "$OUTPUT"
echo "mcp-setup: wrote .mcp.json"
