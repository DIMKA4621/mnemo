import { describe, expect, it } from "vitest";
import type { UpdateApplyState, UpdateProgress } from "../api/settings";
import {
  bumpsOnCount,
  formatMegabytes,
  noteStepProgress,
  progressPercent,
  progressStep,
  stepBar,
  stepResult,
} from "./step-progress";

const MB = 1048576;
const bytes = (current: number, total: number | null): UpdateProgress => ({ current, total, unit: "bytes" });
const packages = (current: number): UpdateProgress => ({ current, total: null, unit: "packages" });
const staging = (progress: UpdateProgress | null, step: UpdateApplyState["step"] = "download"): UpdateApplyState => ({
  state: "staging", tag: "v3.4.1", step, detail: null, error: null, started_at: null, finished_at: null, progress,
});

describe("progressStep", () => {
  it("maps the unit to the step that reports it", () => {
    expect(progressStep(bytes(1, 2))).toBe("download");
    expect(progressStep(packages(3))).toBe("venv");
  });
});

describe("bumpsOnCount", () => {
  it("bumps on a package count only", () => {
    expect(bumpsOnCount(packages(37))).toBe(true);
  });

  it("keeps a download steady, with or without a total", () => {
    expect(bumpsOnCount(bytes(12 * MB, 23 * MB))).toBe(false);
    expect(bumpsOnCount(bytes(12 * MB, null))).toBe(false);
    expect(bumpsOnCount(null)).toBe(false);
  });
});

describe("stepBar", () => {
  it("draws a percentage when the download's size is known", () => {
    expect(stepBar(bytes(12.3 * MB, 23 * MB))).toEqual({ kind: "percent", percent: 53 });
  });

  it("sweeps without bumping for a download of unknown size", () => {
    expect(stepBar(bytes(12 * MB, null))).toEqual({ kind: "sweep", bumpKey: null });
  });

  it("sweeps and bumps on every package count", () => {
    expect(stepBar(packages(37))).toEqual({ kind: "sweep", bumpKey: 37 });
  });

  it("sweeps without data at all", () => {
    expect(stepBar(null)).toEqual({ kind: "sweep", bumpKey: null });
  });
});

describe("progressPercent", () => {
  it("is a whole percent clamped to 0..100", () => {
    expect(progressPercent(bytes(12.3 * MB, 23 * MB))).toBe(53);
    expect(progressPercent(bytes(30 * MB, 23 * MB))).toBe(100);
  });

  it("is null without a known, positive total", () => {
    expect(progressPercent(packages(37))).toBeNull();
    expect(progressPercent(bytes(5, 0))).toBeNull();
    expect(progressPercent(null)).toBeNull();
  });
});

describe("formatMegabytes", () => {
  it("keeps at most one decimal, in the reader's locale", () => {
    expect(formatMegabytes(23 * MB, "en")).toBe("23");
    expect(formatMegabytes(12.34 * MB, "en")).toBe("12.3");
    expect(formatMegabytes(12.34 * MB, "uk")).toBe("12,3");
  });
});

describe("noteStepProgress", () => {
  it("keeps each step's last count once the next step starts", () => {
    let seen = noteStepProgress({}, staging(bytes(5 * MB, 23 * MB)));
    seen = noteStepProgress(seen, staging(bytes(23 * MB, 23 * MB)));
    seen = noteStepProgress(seen, staging(packages(12), "venv"));
    seen = noteStepProgress(seen, staging(packages(64), "venv"));
    expect(seen).toEqual({ download: bytes(23 * MB, 23 * MB), venv: packages(64) });
  });

  it("hands back the same object when nothing changed", () => {
    const seen = noteStepProgress({}, staging(packages(7), "venv"));
    expect(noteStepProgress(seen, staging(packages(7), "venv"))).toBe(seen);
    expect(noteStepProgress(seen, staging(null))).toBe(seen);
    expect(noteStepProgress(seen, null)).toBe(seen);
  });
});

describe("stepResult", () => {
  it("reports the download's full size and the last package count", () => {
    expect(stepResult(bytes(22 * MB, 23 * MB))).toEqual(bytes(23 * MB, null));
    expect(stepResult(bytes(9 * MB, null))).toEqual(bytes(9 * MB, null));
    expect(stepResult(packages(64))).toEqual(packages(64));
    expect(stepResult(undefined)).toBeNull();
  });
});
