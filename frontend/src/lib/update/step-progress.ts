import type { UpdateApplyState, UpdateProgress } from "../api/settings";

export type ProgressStep = "download" | "venv";

/** The last count seen for each staging step — what a finished step shows as
 *  its result once the server has moved on to the next one. */
export type StepProgress = Partial<Record<ProgressStep, UpdateProgress>>;

export function progressStep(progress: UpdateProgress): ProgressStep {
  return progress.unit === "bytes" ? "download" : "venv";
}

/** Folds the record's current count in; the same object back when nothing
 *  changed, so a store setter can skip the update. */
export function noteStepProgress(prev: StepProgress, apply: UpdateApplyState | null | undefined): StepProgress {
  const progress = apply?.progress;
  if (!progress) return prev;
  const step = progressStep(progress);
  const seen = prev[step];
  if (seen && seen.current === progress.current && seen.total === progress.total) return prev;
  return { ...prev, [step]: progress };
}

/** Whether each new count bumps the bar. Only a package count does: a byte
 *  download without a length (codeload's chunked transfer) ticks every few
 *  hundred ms and would never settle, so it keeps the steady sweep. */
export function bumpsOnCount(progress: UpdateProgress | null | undefined): progress is UpdateProgress {
  return progress?.unit === "packages";
}

/** Which bar the active step draws: antd's determinate one when a total is
 *  known, otherwise an indeterminate sweep — bumped per count only for
 *  packages (see `bumpsOnCount`). */
export type StepBar = { kind: "percent"; percent: number } | { kind: "sweep"; bumpKey: number | null };

export function stepBar(progress: UpdateProgress | null | undefined): StepBar {
  const percent = progressPercent(progress);
  if (percent !== null) return { kind: "percent", percent };
  return { kind: "sweep", bumpKey: bumpsOnCount(progress) ? progress.current : null };
}

/** Whole percent, `null` without a known total. */
export function progressPercent(progress: UpdateProgress | null | undefined): number | null {
  if (!progress?.total || progress.total <= 0) return null;
  return Math.max(0, Math.min(100, Math.floor((progress.current / progress.total) * 100)));
}

/** Megabytes (MiB) with at most one decimal, in the reader's locale. */
export function formatMegabytes(bytes: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(bytes / 1048576);
}

/** What a finished step reports: the download's size alone (no "of total"),
 *  or the package count. */
export function stepResult(progress: UpdateProgress | undefined): UpdateProgress | null {
  if (!progress) return null;
  return progress.unit === "bytes" ? { ...progress, current: progress.total ?? progress.current, total: null } : progress;
}
