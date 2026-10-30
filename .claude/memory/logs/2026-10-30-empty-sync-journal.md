# 2026-10-30 — empty index syncs no longer journaled

User's call: «Синхронізація індексу · 0 файлів · 0 видалено» rows are noise.
Measured on the live `service.db`: 6017 `bulk`/`watcher`/`ok` rows against ~470 real file/prune events — the watcher's rescans (directory events, retries) each wrote one.

`api._on_queue_event` now skips the journal row for an `index_done` of kind `bulk` whose `planned_files` and `planned_prunes` are both 0 (`_is_empty_sync`); the WS publish is unchanged, a scan that found changes, a `rebuild`, file/prune rows and every `index_error` are still recorded.
`last_index_error` already ignored `bulk` rows, so nothing reads them for state.
Test: `tests/test_index_journal.py` (not wired into CI, like `test_watcher.py`).

Known gap, not fixed: a bulk row that *did* find changes still shows «0 файлів», because a bulk's counters are 0 by design (the spawned file tasks report their own rows) and `planned_files`/`planned_prunes` are not stored in `index_events`.

The existing rows were deleted from the live `service.db` with the user's approval: every `bulk` row with result `ok`/`skipped` and 0/0 counters (6081 at deletion time) — this includes the few scans that had found changes, which are indistinguishable in the table; their file rows remain.
The live engine runs an older version, so the filter takes effect only after the next `install.ps1`.
