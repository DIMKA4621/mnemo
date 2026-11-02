# 2026-10-30 — MN-58: «Показана версія застаріла» on the first OK, and a real progress bar

Found live updating v3.4.0l → v3.4.1 with the MN-57 fixes already in: the first OK got 409 `stale_target`, a retry 15 s later worked.

Root cause (confirmed in `@tanstack/query-core` 5.102.8 `queryClient.fetchQuery`): `fetchQuery` returns cached data without a request while it is younger than `staleTime`, and the console's client defaults to `staleTime: 15_000` (`lib/query/client.ts`).
So after «Перевірити оновлення» the check note showed the fresh tag from the check response, while `confirmApply()` sent the cached, older `latest_known.tag`, which `_begin_apply` rejects against `last_check`.
Two more drift paths: the background checker writes `last_check` without any WS broadcast, and `update_auto_pending started` only patched `auto.pending`.

Fix: `runCheck` refetches with `staleTime: 0`; entering `confirm` refetches and OK is disabled while it is in flight; `update_auto_pending started` refetches; a `stale_target` refetches and returns to `confirm` with the current tag and «Знайдено новішу версію» (`staleTargetRetryTag`, null when the server names the same tag it just rejected, so it cannot ping-pong).
Lesson: anywhere the console must act on the server's *current* state (a confirm, a check result), `fetchQuery`/`invalidate` with the default `staleTime` is not a read — pass `staleTime: 0`.

The progress phase now shows one antd `<Progress percent={100} status="active" showInfo={false} size="small"/>` (antd has no indeterminate mode); the old hand-written `.upd-step-bar` sweep was removed.
Reduced motion: antd draws the `active` shimmer on the track's `::after`, unreachable by token or `styles`; the track gets `classNames={{ track: "upd-progress-track" }}` and a scoped `prefers-reduced-motion` rule stops it (`!important` needed, antd's selector is more specific).

## MN-59 (2026-11-02) — the update dialog shows real per-step progress

The antd `Progress percent={100} status="active"` stand-in was rejected on sight: a full bar with a faint shimmer every ~2.5 s reads as decoration, not progress (antd has no indeterminate mode).
Now each step owns its bar, under the active step, with its live info and notes directly beneath it, nothing at the modal bottom:

- download — a determinate antd `Progress` from bytes («12,3 / 23 МБ · 53%»); finished step shows «✓ 23 МБ»;
- packages — a hand-written indeterminate sweep (`.upd-sweep`: 35% `--accent` segment on a `--line` track, 1.2 s, `transform` only) plus a flash and counter highlight re-mounted per new count («~37 пакетів»), the sweep itself never restarts;
- byte download without Content-Length — the sweep with a growing «N МБ», **no** per-event flash (review catch: codeload often sends chunked, and a flash per throttled byte event would remount ~4×/s and never settle);
- switching — the sweep; both bars 4 px (`BAR_HEIGHT`); reduced motion freezes everything at a static 40%.

Backend: an additive `progress: {current, total|null, unit: "bytes"|"packages"} | null` on WS `update_progress` and on `apply` in `GET /api/update/status` (contracts §9.7/§9.9); the download streams `resp.num_bytes_downloaded` throttled by `_ByteProgress` (≥0.25 s or each 1% of a known total, final count always sent); venv carries the pip «Collecting» count; every other step and every disk-read state is `null`; staging start also resets `detail`.
A per-step history is not stored server-side — the console keeps finished-step summaries in its store from the last event it saw.
`devserver.py` simulates 23 MB in chunks and 1..64 packages with uneven gaps, and `POST /api/__dev/release {"tag"}` "publishes" a release without a broadcast — the way to test any update UI by hand without a real release.
