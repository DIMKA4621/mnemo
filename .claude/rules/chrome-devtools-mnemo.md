# Chrome DevTools MCP for mnemo — a separate instance (port 9946)

Binding for the team lead and every subagent in this project, same reach as `v3-build.md`.
Project-local only — does not touch the user's global `chrome-devtools` MCP setup (that stays on port 9902, documented in `~/.claude/rules/chrome-devtools-mcp.md`, untouched by this file).

## Why a separate instance

**Confirmed live 2026-08-23:** two concurrent Claude Code sessions, both on the global `chrome-devtools` (port 9902), each spawns its own `npx chrome-devtools-mcp` process.
Both independent processes connect over CDP to the very same Chrome — each keeps its own tab/target cache (Puppeteer) and knows nothing about the other's actions.
Result: races over the active tab, `"Connection closed"`, then `mcp__chrome-devtools__*` tools disappear from the registry — the `chrome-devtools-mcp` process itself crashed with an unhandled exception.

**Researched (GitHub `ChromeDevTools/chrome-devtools-mcp`, issues #926, #1763) — no quick fix exists:** the server only speaks stdio (no HTTP/SSE mode for multiple clients to share one connection), and `--experimentalPageIdRouting` only helps agents that already share ONE already-running server, not two separate processes spawned by two Claude Code sessions.
This is a known, still-unresolved gap in the tool (issue #926), not something a flag can work around.

## Fix: a second, fully separate Chrome instance

- Profile: `C:\Users\dima\.config\claude-chrome-mnemo` — empty, fresh (not a copy of the main profile's logins), sign in manually.
- Port: `9946`.
- Launch helper: `C:\Users\dima\.config\launch-claude-chrome-mnemo.ps1` (same logic as the global script — checks for a busy port, same anti-automation flags).
- MCP server registered **in this project**, in `.mcp.json` (git-ignored, like every other entry there), named `chrome-devtools-mnemo` → tools `mcp__chrome-devtools-mnemo__*`.
- Changing `.mcp.json` requires restarting Claude Code — the new tools only appear in the next session.

## Which one to use when

Work in mnemo itself goes through `chrome-devtools-mnemo` (9946), not the global `chrome-devtools` (9902) — so a mnemo session no longer fights over one `--browserUrl` with a session in any other project.
This doesn't remove the problem entirely: two concurrent sessions inside mnemo itself would still conflict with each other over `chrome-devtools-mnemo` — it just routes mnemo's traffic separately from every other project.
