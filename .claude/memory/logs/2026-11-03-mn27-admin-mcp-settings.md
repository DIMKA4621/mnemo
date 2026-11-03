# 2026-11-03 — MN-27: «Admin MCP» settings section

Settings tab «Admin MCP» between «Модель ембедингу» and «Обслуговування»: description, service token (masked/reveal/copy), the same literal/template switch and snippets as the bank's «Доступ MCP» modal (shared pieces extracted into `frontend/src/components/common/McpAccess.tsx`), and «Додати в .mcp.json…» → folder picker → preview → write.
Machine-level access, so a Settings section, not a bank-menu item (user's choice).

Backend: `GET /api/admin-mcp`, `GET /api/admin-mcp/wiring?root=&mode=` (preview, no writes), `POST /api/admin-mcp/wiring {root, mode}`; logic in `scaffold.py` (`admin_mcp_snippet`, `admin_mcp_preview`, `wire_admin_mcp`), reusing `init`'s template-layer machinery (`_Wiring`, `_bootstrap_layer`, `_plan_setup_scripts`, `_run_setup_script`).
Rules: additive only, an existing `mnemo-admin` of any shape is never overwritten, refuse when `.mcp.json` (literal) or `.mcp.json`/`.mcp.env` (template) is git-tracked, `suggested_mode=template` when the project already has the layer.

Review caught a real leak: the first version normalised only `mnemo-memory` when seeding a template from an existing `.mcp.json`, so a legacy `mnemo` key with a literal bank token was copied verbatim into the git-tracked template.
Fix: seed through `init`'s own `_plan_servers` (no second copy of the rename/drop rule); an entry `init` rewrites only under `--migrate` now refuses template mode with reason `needs_migrate`.
Lesson: when a new path needs the same normalisation `init` already does, call `init`'s planner — a re-implementation drifted on the first try.

Known gap (follow-up ticket): other banks' mnemo entries (e.g. `mnemo-notes` with `?token=<hex>`) are still copied verbatim into a new template — same as `init`. mnemo entries are recognised by shape (`_is_mnemo_http`: `type: http`, URL containing `/mcp`; `_TOKEN_IN_URL`: `[?&]token=<32+ hex>`), not by key name.
