import type { UpdateApplyState, UpdateStatusResult } from "../api/settings";

/** How long auto-pending waits, after the countdown vanished with no sign of
 *  a new apply, before reading it as a cancellation rather than the gap
 *  between the timer settling and staging being recorded. */
export const AUTO_SETTLE_GRACE_MS = 6000;

export interface UpdateProgressEvent {
  step?: UpdateApplyState["step"];
  tag?: string;
  detail?: string | null;
  error?: string | null;
}

export function isApplyBusy(apply: UpdateApplyState | null | undefined): boolean {
  return apply?.state === "staging" || apply?.state === "switching";
}

export function isApplyTerminal(apply: UpdateApplyState | null | undefined): boolean {
  return apply?.state === "done" || apply?.state === "failed" || apply?.state === "rolled_back";
}

/** True when `apply` is the very record `superseded` captured — the outcome
 *  of an earlier attempt, not of the one just started. */
export function isSupersededApply(
  apply: UpdateApplyState | null | undefined,
  superseded: UpdateApplyState | null,
): boolean {
  if (!apply || !superseded) return false;
  return (
    apply.state === superseded.state &&
    apply.tag === superseded.tag &&
    apply.started_at === superseded.started_at &&
    apply.finished_at === superseded.finished_at
  );
}

/** The guard only covers the gap before the backend records the new attempt;
 *  a server record with its own `started_at` ends it. */
export function supersededAfter(
  apply: UpdateApplyState | null | undefined,
  superseded: UpdateApplyState | null,
): UpdateApplyState | null {
  if (!superseded || !apply?.started_at) return superseded;
  return apply.started_at === superseded.started_at ? superseded : null;
}

export function visibleApply(
  apply: UpdateApplyState | null | undefined,
  superseded: UpdateApplyState | null,
): UpdateApplyState | null {
  if (!apply) return null;
  return isSupersededApply(apply, superseded) ? null : apply;
}

/** What the cache holds between clicking OK and the backend's own staging
 *  thread recording `staging` — `POST /api/update/apply` returns before it
 *  does, so a status read in that gap still answers with the last outcome. */
export function stagingPlaceholder(tag: string, trigger: "manual" | "auto"): UpdateApplyState {
  return {
    state: "staging", tag, step: null, detail: null, error: null,
    started_at: null, finished_at: null, trigger,
  };
}

/** Starting an attempt: the earlier outcome to set aside, and the record the
 *  cache should hold now. Something already busy, or an outcome that is not
 *  the one set aside, is fresher than a placeholder and is kept. */
export function seedAttempt(
  cached: UpdateApplyState | null | undefined,
  tag: string,
  trigger: "manual" | "auto",
  superseded: UpdateApplyState | null,
): UpdateApplyState | null | undefined {
  if (isApplyBusy(cached)) return cached;
  if (isApplyTerminal(cached) && !isSupersededApply(cached, superseded)) return cached;
  return stagingPlaceholder(tag, trigger);
}

export function rejectedApply(tag: string, message: string): UpdateApplyState {
  return {
    state: "failed", tag, step: null, detail: null, error: message,
    started_at: null, finished_at: null, trigger: "manual",
  };
}

/** A WS `update_progress` folded into the cached record. Arriving on top of
 *  a finished record it starts a new one, so nothing from the earlier
 *  outcome (timestamps, error) carries over into it. */
export function applyProgressEvent(old: UpdateApplyState, event: UpdateProgressEvent): UpdateApplyState {
  const base = isApplyTerminal(old)
    ? { ...old, started_at: null, finished_at: null, error: null }
    : old;
  return {
    ...base,
    tag: event.tag ?? base.tag,
    step: event.step ?? base.step,
    detail: event.detail ?? null,
    state: event.step === "failed" ? "failed" : event.step === "done" ? "switching" : "staging",
    error: event.step === "failed" ? (event.error ?? null) : base.error,
  };
}

export type AutoPendingNext = "stay" | "progress" | "terminal" | "idle";

/** Auto-pending's re-sync: where the modal goes after a status read.
 *  `pendingGoneMs` is how long the countdown has been absent (0 while it is
 *  still armed). A vanished countdown with the old record still in place is
 *  either the settle-to-staging gap or a cancellation — waited out briefly,
 *  then read as the latter. */
export function autoPendingNext(
  data: Pick<UpdateStatusResult, "apply" | "auto">,
  superseded: UpdateApplyState | null,
  pendingGoneMs: number,
): AutoPendingNext {
  if (isApplyBusy(data.apply)) return "progress";
  if (data.auto.pending) return "stay";
  // Without a captured record there is no telling a fresh outcome from an old one.
  if (superseded && isApplyTerminal(data.apply) && !isSupersededApply(data.apply, superseded)) return "terminal";
  if (isSupersededApply(data.apply, superseded) && pendingGoneMs < AUTO_SETTLE_GRACE_MS) return "stay";
  return "idle";
}
