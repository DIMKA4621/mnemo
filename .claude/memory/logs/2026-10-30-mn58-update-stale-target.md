# 2026-10-30 — MN-58: «Показана версія застаріла» on the first OK, and a real progress bar

Found live updating v3.4.0l → v3.4.1 with the MN-57 fixes already in: the first OK got 409 `stale_target`, a retry 15 s later worked.

Root cause (confirmed in `@tanstack/query-core` 5.102.8 `queryClient.fetchQuery`): `fetchQuery` returns cached data without a request while it is younger than `staleTime`, and the console's client defaults to `staleTime: 15_000` (`lib/query/client.ts`).
So after «Перевірити оновлення» the check note showed the fresh tag from the check response, while `confirmApply()` sent the cached, older `latest_known.tag`, which `_begin_apply` rejects against `last_check`.
Two more drift paths: the background checker writes `last_check` without any WS broadcast, and `update_auto_pending started` only patched `auto.pending`.

Fix: `runCheck` refetches with `staleTime: 0`; entering `confirm` refetches and OK is disabled while it is in flight; `update_auto_pending started` refetches; a `stale_target` refetches and returns to `confirm` with the current tag and «Знайдено новішу версію» (`staleTargetRetryTag`, null when the server names the same tag it just rejected, so it cannot ping-pong).
Lesson: anywhere the console must act on the server's *current* state (a confirm, a check result), `fetchQuery`/`invalidate` with the default `staleTime` is not a read — pass `staleTime: 0`.

The progress phase now shows one antd `<Progress percent={100} status="active" showInfo={false} size="small"/>` (antd has no indeterminate mode); the old hand-written `.upd-step-bar` sweep was removed.
Reduced motion: antd draws the `active` shimmer on the track's `::after`, unreachable by token or `styles`; the track gets `classNames={{ track: "upd-progress-track" }}` and a scoped `prefers-reduced-motion` rule stops it (`!important` needed, antd's selector is more specific).
