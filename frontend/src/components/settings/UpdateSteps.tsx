"use client";

import { Progress } from "antd";
import { useLocale } from "next-intl";
import { useT } from "@/lib/i18n/hooks";
import type { UpdateApplyState, UpdateProgress } from "@/lib/api/settings";
import {
  formatMegabytes,
  progressStep,
  stepBar,
  stepResult,
  type StepProgress,
} from "@/lib/update/step-progress";
import "./settings.css";

const UPDATE_STEPS = [
  { key: "download", labelKey: "update.steps.download" },
  { key: "venv", labelKey: "update.steps.venv" },
  { key: "switching", labelKey: "update.steps.switching" },
] as const;

function stepIndex(apply: UpdateApplyState | null): number {
  if (apply?.state === "switching") return 2;
  if (apply?.step === "venv") return 1;
  return 0; // 'download', or staging with no step reported yet
}

function useProgressText() {
  const t = useT();
  const locale = useLocale();
  return (progress: UpdateProgress): string => {
    if (progress.unit === "packages") return t("update.progress.packages", { n: progress.current });
    const current = formatMegabytes(progress.current, locale);
    if (progress.total === null) return t("update.progress.size", { size: current });
    return t("update.progress.sizeOfTotal", { current, total: formatMegabytes(progress.total, locale) });
  };
}

/** antd has no indeterminate mode: an empty track with a segment sweeping
 *  across it. A bump flash keyed on the count replays over the segment
 *  without restarting its sweep. */
function SweepBar({ label, bumpKey }: { label: string; bumpKey: number | null }) {
  return (
    <div className="upd-sweep" role="progressbar" aria-label={label}>
      <span className="upd-sweep-track">
        <span className="upd-sweep-seg" />
      </span>
      {bumpKey !== null && <span key={bumpKey} className="upd-sweep-flash" />}
    </div>
  );
}

// Track height shared by antd's determinate bar and the sweep (`.upd-sweep`).
const BAR_HEIGHT = 4;

function ActiveStepBar({
  progress,
  label,
  note,
}: {
  progress: UpdateProgress | null;
  label: string;
  note: string | null;
}) {
  const text = useProgressText();
  const bar = stepBar(progress);
  const bumpKey = bar.kind === "sweep" ? bar.bumpKey : null;
  return (
    <div className="upd-step-body">
      {bar.kind === "percent" ? (
        <Progress
          percent={bar.percent}
          status="active"
          showInfo={false}
          size={[-1, BAR_HEIGHT]} // -1: antd's own "fill the width"
          aria-label={label}
          classNames={{ root: "upd-step-progress", track: "upd-progress-track" }}
        />
      ) : (
        <SweepBar label={label} bumpKey={bumpKey} />
      )}
      {progress && (
        <span key={bumpKey ?? "steady"} className={bumpKey !== null ? "upd-step-meta upd-bump" : "upd-step-meta"}>
          {text(progress)}
          {bar.kind === "percent" && ` · ${bar.percent}%`}
        </span>
      )}
      {note && <span className="upd-step-note">{note}</span>}
    </div>
  );
}

/** `activeNote` is any further line about the running step (a backend's own
 *  detail, the restart warning), shown under its bar with the rest. */
export function UpdateSteps({
  apply,
  stepProgress,
  activeNote,
}: {
  apply: UpdateApplyState | null;
  stepProgress: StepProgress;
  activeNote: string | null;
}) {
  const t = useT();
  const text = useProgressText();
  const activeIndex = stepIndex(apply);
  return (
    <div className="upd-steps">
      {UPDATE_STEPS.map((step, i) => {
        const status = i < activeIndex ? "done" : i === activeIndex ? "active" : "pending";
        const label = t(step.labelKey);
        const result = step.key === "switching" ? null : stepResult(stepProgress[step.key]);
        const live =
          apply?.progress && apply.state === "staging" && progressStep(apply.progress) === step.key ? apply.progress : null;
        return (
          <div key={step.key} className={`upd-step is-${status}`}>
            <div className="upd-step-head">
              <span className="upd-step-mark">{status === "done" ? "✓" : i + 1}</span>
              <span className="upd-step-label">{label}</span>
            </div>
            {status === "active" && <ActiveStepBar progress={live} label={label} note={activeNote} />}
            {status === "done" && result && (
              <div className="upd-step-body">
                <span className="upd-step-meta">✓ {text(result)}</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
