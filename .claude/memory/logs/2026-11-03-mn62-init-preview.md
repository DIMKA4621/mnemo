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

Finding, not fixed (init behaviour was out of scope): an unparsable `.mcp.json` with no template is **not** a refusal — `_bootstrap_layer` skips the seed, the template starts empty, and the setup script then overwrites that `.mcp.json` from the template, dropping whatever it held.
The preview reports it faithfully as `.mcp.json` `update`/`regenerate`.
