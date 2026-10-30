# 2026-10-30 — MN-57: console self-update UI showed a stale outcome

Found live updating this machine v3.3.0l → v3.4.0 from the console; the update itself succeeded (`apply.state: done`, `error: null`).

- **False «Оновлення не вдалося» right after OK.** `confirmApply()` entered `progress`, and the progress effect read the cached `apply` of the *previous* attempt — a terminal `failed` — before the backend had written `staging`: `POST /api/update/apply` answers 202 before `_run_staged_apply` touches `_apply_progress`, so for a moment every status read still returns the old outcome.
- **Second click → 409 `update_in_progress`** shown as a failure; the server was right, the modal now refetches and resumes the running update's progress.
- **Sidebar banner stuck on «триває…».** Status was polled only while the modal was in `progress`; after the service restart the WS `hello` refetch caught `switching` before `done` was recorded, and nothing asked again.
  The status query now polls every 2 s whenever the cached apply is `staging`/`switching`, in any modal phase (behaviour change: it keeps polling if the backend never comes back while the cache says busy).

Fix (frontend, `lib/update/apply-state.ts` + `UpdateModal.tsx`, `dispatch.ts`, `update-modal` store): on OK the cached terminal record is set aside as `supersededApply` and never shown as this attempt's result; the guard is dropped on the first evidence of the new attempt (catch path, first WS `update_progress`, any server record with a different non-null `started_at`).
Review caught a hole in the first cut: the WS patch inherited the old `started_at`/`finished_at`, so a fast real failure could be identical to the superseded record and hidden; `applyProgressEvent` now starts a fresh record over a terminal one.
A server-timer auto-apply now lands in progress via `autoPendingNext` with a 6 s settle grace, instead of closing the modal.

Not a bug, my own mistake: the «вѿтановленнѿ» mojibake I reported came from reading the JSON through `curl | python` on Windows (piped stdin decodes as cp1252 + surrogateescape); the API sends correct UTF-8.
A regression test now pins that a Cyrillic `detail` reaches `GET /api/update/status` intact.

Open, not done: the root fix is on the backend — `_begin_apply` could set `state="staging"` with a fresh `started_at` synchronously before `thread.start()`, so the 202 is never ahead of the state it reports; that would retire most of the superseded machinery and close a remaining narrow path (a locally synthesised rejection in the cache when OK is pressed).
