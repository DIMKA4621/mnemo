# 2026-08-24 — MN-11: graceful stop on Windows + bounded fast-retry for interrupted rebuilds

Branch `feature/mn-11-graceful-stop-and-rebuild-retry`.
Files: `src/api.py`, `src/config.py`, `src/service_ctl.py`, `src/watcher.py`, `src/workqueue.py`, `tests/test_watcher.py` (new), `tests/test_service_ctl.py`.

## The bug this fixes

`mnemo service stop` on Windows did `taskkill /PID <pid> /T /F` immediately — a hard force-kill that never let FastAPI's `lifespan` shutdown run, so `workqueue.stop()` never executed.
An in-flight `bulk`/`rebuild` task (which wipes a bank's rows via `store.reset_index()` *before* re-embedding) could be killed leaving the bank permanently at 0 chunks/files until a manual `mnemo reindex --bank <name> --full`.
This exact failure mode was already observed and recorded in `logs/2026-08-21-port-renumber.md`.
POSIX already worked correctly — `SIGTERM` reaches uvicorn's graceful shutdown there.

Separately, a bank stuck failing with `EmbeddingUnavailable` only self-healed via the existing 900s (15min) watcher rescan or a service restart, with no cap — a permanently broken provider would retry forever, silently, every 15 min.

## Graceful stop mechanism

Windows has no signal uvicorn listens for the way POSIX's `SIGTERM` works, so the fix goes through HTTP instead of a signal:

- `api.py::run()` now builds an explicit `uvicorn.Config`/`Server` (was a bare `uvicorn.run()`), storing the server instance on `app.state.uvicorn_server`.
- New `POST /api/shutdown` (plain `/api/*` route, no special auth code needed — it's covered automatically by the existing prefix-matched `auth_middleware`/`_configured_token()` gate).
  Flips `server.should_exit = True` via `call_soon` (not synchronously) so the `{"ok": true}` response reaches the caller before teardown starts.
  This is the *exact* flag SIGTERM already sets on POSIX, so the existing `_shutdown()` path (watcher.stop() → workqueue.stop() → servicelog.close() etc.) runs unmodified regardless of which path set it.
- `service_ctl.py::stop()`'s Windows branch: before the existing `_terminate_tree(pid)`, POST `/api/shutdown` (2s connect/read timeout, `Authorization: Bearer` only if a token is actually configured — mirrors `client.py`'s exact pattern, the previously-fixed empty-Bearer bug was checked and not reintroduced).
  On success, poll for process death within the *existing* `SERVICE_STOP_TIMEOUT` (10s default) — deliberately no second timeout knob, single shared budget for graceful+fallback, per explicit user decision.
  Any failure (unreachable, timeout, old backend without the route) falls straight through to the unchanged `_terminate_tree` force-kill.
  POSIX path untouched.
- **Must poll both the spawned pid and the `service.json`-published pid.** The spawned pid on Windows is the `pythonw.exe` redirector stub, which can die before the real server process does — polling only the spawned pid risked reporting success before the real server/port was actually free.
  Caught live during platform-dev's own manual verification before it became a test; now has a dedicated regression test (`test_graceful_shutdown_waits_for_both_redirector_pids`).
- `workqueue.py`'s `_run_file`'s `should_yield()` now also yields on `_stop.is_set()` regardless of priority (previously only LOW-priority yielded, and only when a HIGH task was waiting).
  This bounds the graceful window to one batch (~16 chunks) instead of "run the whole file to completion," which is what makes the 10s timeout budget realistic even for a large file.
  Resume-on-restart reuses the existing `start_batch` mechanism unchanged.

Live-verified worst case: a rebuild interrupted at chunks=0 (right after `reset_index()`, before any re-embedding) → graceful `stop()` (5.25s) → fresh backend on the same state dir → bank fully recovered to pre-interruption file/chunk counts.
Idle stop unaffected (0.97s, zero `_terminate_tree` calls).
Fallback-to-force-kill still works when `/api/shutdown` is unreachable (2.7s, exactly one `_terminate_tree` call).

## Bounded fast-retry for banks stuck on EmbeddingUnavailable

Key finding that narrowed the original scope: `store.reset_index()` also drops the `files` table (hashes), so ANY subsequent scan (restart or the periodic rescan) already sees every file as "changed" and re-indexes it whole — self-healing already existed, just bounded by the 900s rescan interval and with no retry cap.

Architecture (all in `watcher.py`, inside the existing `_loop()`):
- The 900s full rescan (`rescan_interval_s()`) is completely untouched — still walks every watched bank regardless of error state.
- A second, independent, much shorter tick: `MNEMO_RETRY_INTERVAL_S` (30s default).
  On every tick, for banks with an **unclosed streak of `EmbeddingUnavailable` errors** in recent `index_events` where `0 < streak < MNEMO_RETRY_MAX_ATTEMPTS` (5 default), fire a targeted `enqueue_bulk` for just that bank.
  No separate "who's retrying" registry — fully derived each tick from the same streak read that defines the cap, so a healthy bank (streak=0) or a capped-out bank (streak≥max) naturally drops out.
- Deterministic errors (e.g. dimension mismatch) never enter the streak at all — this reuses the exact existing signal from `workqueue._execute()`: `EmbeddingUnavailable`'s handler stores `error=str(exc)` bare, while the generic `except Exception` handler stores `f"{type(exc).__name__}: {exc}"` (class-name-prefixed).
  The classifier (`_is_embedding_unavailable_error`) treats "does NOT match the `<Identifier>: ` prefix shape" as the positive signal — string-shape-based, not a typed field, because `index_events` has no column recording which exception branch produced a row.
  Verified against all real `raise EmbeddingUnavailable(...)` message shapes in `providers/local.py`, `providers/api.py`, `embed_server.py`.
- Explicit triggers (`api`/`cli`/`mcp`/`ui`) always bypass the cap, and their own resulting `index_events` entry is what the next tick's streak-read naturally sees — no special-case reset code needed.
- **A real bug found in first review pass, fixed in a second round:** the streak read was capped at a fixed `_RETRY_SCAN_LIMIT = 50` events, independent of the user-configurable `MNEMO_RETRY_MAX_ATTEMPTS`.
  If someone configured the cap above 50, the streak could never be observed past 50, so `streak < max_attempts` (e.g. `50 < 200`) stayed true forever — the exact unbounded-retry bug this ticket exists to prevent, just moved to a higher, non-default threshold.
  Fixed: `_retry_scan_limit() = max(50, retry_max_attempts() + 10)` — the scan window now always tracks the configured cap with margin.
  Not hit at the shipped default (5 ≪ 50), but was a real latent trap, not hypothetical — a second review pass traced the edge cases (0, negative, unparseable configured value) by hand and confirmed no other way to under-count an unbounded streak.

**Known, accepted, non-blocking caveat (tracked for a future ticket, not fixed here):** the admission check (`0 < streak < max_attempts`) is evaluated per watcher tick, not synchronized against overlapping rescan rounds.
With a very short `MNEMO_RETRY_INTERVAL_S` (reproduced at 2s, well below the 30s default) and slow-failing attempts, a second rescan round can start (the `_scanning` guard blocks a concurrent *scan*, not a second round of already-enqueued file tasks from a prior round still draining) and the streak can overshoot the cap by one extra round (reproduced: 6 instead of cap=3) before the next tick's check catches up.
Self-limiting, not infinite — confirmed by reading the code in review, not just trusted from the tester's report.
Unlikely at the shipped 30s default with typically-fast connection-refused-style failures.

## Process notes worth remembering

- **First review pass caught a real bug a from-scratch implementation missed**, exactly the value this repo's review-before-merge discipline is meant to provide: the scan-window-vs-configured-cap mismatch wasn't visible to either implementing agent because the default (5) never exercises it — it only shows up when someone configures the cap higher than the hardcoded scan window.
  A second, narrower re-review pass then independently re-verified both the bug fix and the new test coverage by reading the code and re-running the suite, not by trusting the fix report.
- **`~/.mnemo/versions/v3.0.10/.venv` is a broken/half-built venv** — only `Scripts/pythonw.exe` present, no `python.exe`, no `pyvenv.cfg` (v3.0.9 right below it is a complete, working venv).
  Found independently by two different agents (service-dev-fix and platform-dev-fix) while trying to run tests via the installed engine.
  This is why `bin\mnemo.exe status/doctor/--help` would silently exit 1 with zero output if `current` points at v3.0.10 — the redirector can't find `python.exe`.
  Not caused by or related to MN-11's work; unclear what left it half-built (possibly an interrupted self-update or install run).
  **Needs investigation as a separate task** — worth checking whether `current` actually points there and whether the live production service is affected (it wasn't touched during this session's testing — confirmed healthy throughout via `/health` on port 4646, pid 20920).
