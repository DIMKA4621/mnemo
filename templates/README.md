# mnemo templates

Drop-in starting points for a project that **adopts** mnemo (not for
developing mnemo itself).

## Placeholder convention

- `{{MNEMO_BIN}}` — absolute path to the installed launcher, e.g.
  `/home/<you>/.claude/mnemo/bin/mnemo`. Substitute it when copying the
  `.json` templates (they are valid JSON and carry no comments).
- `<ANGLE_BRACKETS>` / `<!-- … -->` — fill-in points and guidance in the
  markdown templates. Remove the guidance comments once filled.

## Files

| Template | Copy to | Purpose |
|---|---|---|
| `CLAUDE.md.template` | append into your project `CLAUDE.md` | tells agents to use the memory |
| `memory/MEMORY.md.template` | `<proj>/.claude/memory/MEMORY.md` | thin memory index skeleton |
| `agent-memory/ROLE/MEMORY.md.template` | `<proj>/.claude/agent-memory/<role>/MEMORY.md` | per-agent memory block |
| `agents/role.md.template` | `<proj>/.claude/agents/<role>.md` | subagent that uses mnemo |
| `mcp.json.template` | `<proj>/.mcp.json` | registers the mnemo MCP server |
| `claude-settings.json.template` | `<proj>/.claude/settings.json` | reindex hooks |

## Setup order

1. Install the mnemo engine once at user scope and run `mnemo warmup`.
2. Copy `mcp.json.template` + `claude-settings.json.template`, replace
   `{{MNEMO_BIN}}`.
3. Append `CLAUDE.md.template` into your project `CLAUDE.md`.
4. Seed `.claude/memory/MEMORY.md` from the memory template; add agents
   as needed from the agent templates.
5. Open the project in Claude Code — `SessionStart` builds the index.
