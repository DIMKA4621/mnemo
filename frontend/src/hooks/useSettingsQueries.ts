"use client";

import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import {
  getAutostart,
  getDoctor,
  getEmbedState,
  getSettings,
  getUpdateStatus,
} from "@/lib/api/settings";

export function useSettings() {
  return useQuery({
    queryKey: queryKeys.settings.all,
    queryFn: getSettings,
  });
}

/** Its own request, its own failure (costs a `schtasks`/`systemctl`
 *  subprocess) — fetched once when the General tab opens, never polled. */
export function useAutostart(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.autostart.all,
    queryFn: getAutostart,
    enabled,
  });
}

/** Polls `GET /api/embed/state` every ~3s while a `warmup --force` download
 *  is running (contract correction, MN-36 planning: this is NOT a WS event)
 *  — otherwise a single fetch. `refetchInterval` reads the *previous*
 *  response's own `download.active`, so the poll turns itself off the
 *  moment a download finishes without any component wiring a timer. */
export function useEmbedState(enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.embedState.all,
    queryFn: getEmbedState,
    enabled,
    refetchInterval: (query) => (query.state.data?.download.active ? 3000 : false),
  });
}

/** Costs a real GitHub round trip's worth of backend work for the caller's
 *  own timeout budget — fetched only on an explicit `refetch()` (Maintenance
 *  tab's «Оновити» button), never automatically. */
export function useDoctor() {
  return useQuery({
    queryKey: queryKeys.doctor.all,
    queryFn: getDoctor,
    enabled: false,
  });
}

/** Plain single fetch, seeded once at shell boot (`UpdateModal` mounts at
 *  shell level) — the `update_progress`/`update_auto_pending` WS handlers in
 *  `lib/ws/dispatch.ts` patch this same cache entry directly, and
 *  `useUpdateProgressPolling`/`useAutoPendingPolling` below add polling only
 *  while their own modal phase is actually open. No background polling by
 *  default. */
export function useUpdateStatus() {
  return useQuery({
    queryKey: queryKeys.updateStatus.all,
    queryFn: getUpdateStatus,
  });
}

/** A second observer on the same query key, active only while
 *  `UpdateModal`'s phase is `'progress'` — `GET /api/update/status` is the
 *  sole source of truth for the outcome once the WS channel dies mid-switch
 *  (see that component's docstring). `retry: false`: a failed poll during
 *  the switch window is expected and silent, not a reason to burn extra
 *  attempts faster than the plain 2s cadence. */
export function useUpdateProgressPolling(enabled: boolean) {
  useQuery({
    queryKey: queryKeys.updateStatus.all,
    queryFn: getUpdateStatus,
    enabled,
    refetchInterval: enabled ? 2000 : false,
    retry: false,
  });
}

/** Same mechanism as `useUpdateProgressPolling`, active only while the
 *  auto-pending countdown modal is open — the re-sync half of that phase
 *  (settlement/cancellation can happen without this tab doing anything). */
export function useAutoPendingPolling(enabled: boolean) {
  useQuery({
    queryKey: queryKeys.updateStatus.all,
    queryFn: getUpdateStatus,
    enabled,
    refetchInterval: enabled ? 2000 : false,
    retry: false,
  });
}
