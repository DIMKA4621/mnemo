# 2026-11-03 — MN-62: preview of `mnemo init` in the add-bank dialog (backend)

The add-bank dialog's «підключити проєкт (MCP)» ran a full `init` blind, after the bank was already registered; it now gets a preview first, the same idea as MN-27's Admin MCP preview.

Backend: `GET /api/init-preview?root=<bank root>&create_structure=<bool>` (private, hidden from OpenAPI) → `scaffold.init_preview(project_root)` plus the bank-level checks in `api.py` (`not_project_shaped` → `skipped`, `root_not_found` without `create_structure`, `bank_exists`).
Shape and reason codes: `docs/Memory-contracts-v3.md` §9, right after `POST /api/banks`.

Decisions:

- Path is `/api/init-preview`, not `/api/banks/preview-init`: a literal under `/api/banks/` shadows `GET /api/banks/{bank_id}` for a bank whose *name* is that word (refs resolve by name too).
- No parallel predictor: `_seed_tree` was split into `_plan_seed` (plan) + apply, and the preview runs `_plan_seed` + `_plan_wiring` with `_TOKEN_PROBE`, exactly what `init_project` runs.
  `_Refuse` now carries a `reason` code (`invalid_json`, `needs_migrate`, `unrecognised_entry`, `index_unreadable`; `_EnvConflict` → `env_conflict`); CLI messages unchanged.
- The `.mcp.json` row is a forecast of the platform setup script: only mnemo's own script is read for its one failure mode (a template placeholder with no `.mcp.env` key → `setup_missing_values`, no `.mcp.json`); a foreign script is assumed to succeed.
- Guarantee test (`test_platform.py::test_init_preview_matches_init`): for fresh / `.mcp.json` with foreign + another-bank servers / existing template layer / re-wire with an edited rule and a missing value / tracked `.mcp.json`, the preview leaves the tree byte-identical and its `writes` equal the real `init_project` tree diff.

Finding, fixed in MN-63: an unparsable `.mcp.json` with no template used to be silently skipped by `_bootstrap_layer` — the template started empty and the setup script then overwrote `.mcp.json`, dropping whatever it held.
It is now an `_InvalidMcpJson` refusal (`invalid_json`, line/column from the parser; also non-object top level, non-object `mcpServers`, non-UTF-8; a whitespace-only file counts as absent).
`init --overwrite-invalid` / `POST /api/banks {"overwrite_invalid": true}` / `GET /api/init-preview?overwrite_invalid=true` back it up byte-for-byte to the first free `.mcp.json.bak[.N]`, add `.mcp.json.bak*` to `.gitignore`, then proceed as if `.mcp.json` were absent.
The backup is a planned write in `_Wiring.writes` (bytes, via `_Wiring.backup`), so the preview still derives from init's own plan.
Reviewer catch: the `.gitignore` edit and the backup go at the **head** of the write order — once the template exists a plain re-run no longer refuses, so a backup that fails after the template landed would let the next run drop the servers.
`.mcp.json` is decoded `utf-8-sig`, since Windows PowerShell 5.1 writes a BOM.
Follow-up (2026-11-04): the same refusal/override now holds where `.mcp.json.template` already exists — the setup script regenerates `.mcp.json` there too, dropping whatever only the broken file held (`_usable_mcp_json`, called from `_plan_wiring` with or without the layer).
Admin MCP needed no change: `_admin_plan` already reads `.mcp.json` through `_load_json` in both modes and refuses `invalid_json`, with no override.
