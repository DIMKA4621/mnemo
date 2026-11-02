# 2026-11-02 — MN-31 queue honesty, MN-60 bank rename, MN-54 journal text filter

One branch, `fix/mn-31-mn-60-mn-54-banks-journal`, one PR.
MN-14 (vague "write system logs") closed by the user's decision; MN-27 (admin-MCP wiring button) deferred to later pages.

## MN-31 — every bank showed «індексується, у черзі 4» with nothing changed

Reproduced live on v3.5.0: `/api/status` `queue.by_bank` gave `{depth: 4, indexing: false}` for 7 untouched banks while one heavy bank was building.

- Cause: `watcher._loop` enqueues a safety-net `bulk` for every bank every `RESCAN_INTERVAL_S` (900 s); `workqueue.enqueue` deduplicated only `file`/`prune` (path key), so bulks piled up (≈ one per 15 min of uptime); at LOW they waited behind the heavy bank's LOW file tasks; and the console counts any waiting task as "indexing / свіжі зміни доїжджають".
- Fix: one waiting `bulk`/`rebuild` per bank (rebuild absorbs bulk, an explicit api/cli trigger beats watcher/startup so a frozen-bank skip can't swallow a user's sync); bulk/rebuild dispatch at NORMAL (only spawning the scanner — the files it streams stay LOW); a lone waiting `bulk` is excluded from `depth`, `by_bank` and the totals; an empty bank with a waiting bulk still reads `indexing`, not `empty`.
- Found on the way, fixed too: a rebuild arriving while a scan of the same bank was in flight wiped the index and then skipped itself, leaving the bank half-empty until the next rescan.
  Now the in-flight check runs before `_open_for_rebuild`; the rebuild parks in `_parked` and `_release_scan()` re-queues it when the scan thread exits (no timer, no polling).
- Known, accepted: `mnemo reindex` now prints "0 waiting" right after queueing a sync; aging rescues only LOW, so a NORMAL scan could in theory be starved by a non-stop HIGH stream.

## MN-60 — rename a bank

The backend already allowed it (`PATCH /api/banks/{id}` `{name}`, trimmed, case-insensitive unique, 409 `bank_exists`); only the console was missing.
`RenameDialog.tsx` from the card's `···` menu.
The name is *not* only cosmetic: CLI `--bank`, `banks freeze|…`, admin MCP and `/mcp-tools` address a bank by name, so after a rename the old name fails loudly (never re-routed) — the hint says so.
Nothing persisted keys on the name: wiring is the token, selection/deep link and journal rows use the id; the Journal bank filter was switched to the id as part of MN-54.

## MN-54 — Journal text filter

`GET /api/logs?q=` — substring of `query` (kind=query) or `path` (kind=index; pathless bulk rows drop out while filtering).
Unicode case-insensitive via a `casefold` SQLite UDF registered on `servicelog._reader()` and `instr(casefold(col), ?) > 0` — `instr` makes `%`/`_`/`\` literal with no escaping, which `LIKE` (ASCII-only case folding) could not give for Cyrillic.
No index use: worst case ≈0.7 s per debounced keystroke at the 200k-row cap, accepted.
Console: per-tab search field, 300 ms debounce, `placeholderData` within a tab, first match highlighted.
