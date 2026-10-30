import { describe, expect, it } from "vitest";
import type { UpdateApplyState, UpdateStatusResult } from "../api/settings";
import {
  AUTO_SETTLE_GRACE_MS,
  applyProgressEvent,
  autoPendingNext,
  isApplyBusy,
  isApplyTerminal,
  isSupersededApply,
  rejectedApply,
  seedAttempt,
  stagingPlaceholder,
  staleTargetRetryTag,
  supersededAfter,
  visibleApply,
} from "./apply-state";

const previousFailure: UpdateApplyState = {
  state: "failed", tag: "v3.4.0", step: "failed", detail: null, error: "boom",
  started_at: "2026-10-29T10:00:00+03:00", finished_at: "2026-10-29T10:01:00+03:00", trigger: "manual",
};

const idle: UpdateApplyState = {
  state: "idle", tag: null, step: null, detail: null, error: null, started_at: null, finished_at: null,
};

describe("isSupersededApply", () => {
  it("matches the exact record captured before the new attempt", () => {
    expect(isSupersededApply({ ...previousFailure }, previousFailure)).toBe(true);
  });

  it("does not match the new attempt's own failure for the same tag", () => {
    const fresh = { ...previousFailure, started_at: "2026-10-30T09:00:00+03:00", finished_at: "2026-10-30T09:00:05+03:00" };
    expect(isSupersededApply(fresh, previousFailure)).toBe(false);
  });

  it("is false with nothing captured", () => {
    expect(isSupersededApply(previousFailure, null)).toBe(false);
    expect(isSupersededApply(null, previousFailure)).toBe(false);
  });
});

describe("supersededAfter", () => {
  it("keeps the guard through the 202 gap", () => {
    expect(supersededAfter(stagingPlaceholder("v3.4.0", "manual"), previousFailure)).toBe(previousFailure);
    expect(supersededAfter({ ...previousFailure }, previousFailure)).toBe(previousFailure);
  });

  it("drops it once the server reports the new attempt", () => {
    const staging = { ...previousFailure, state: "staging" as const, started_at: "2026-10-30T09:00:00+03:00", finished_at: null };
    expect(supersededAfter(staging, previousFailure)).toBeNull();
  });
});

describe("(a) a WS failure after a poll re-landed the old outcome", () => {
  it("shows the new attempt's own error", () => {
    let superseded: UpdateApplyState | null = previousFailure;
    let cached = seedAttempt(previousFailure, "v3.4.0", "manual", superseded)!;
    expect(cached.state).toBe("staging");

    cached = { ...previousFailure }; // a poll answered inside the 202 gap
    expect(visibleApply(cached, superseded)).toBeNull();

    cached = applyProgressEvent(cached, { step: "download", tag: "v3.4.0" });
    superseded = null; // the first `update_progress` is evidence of the new attempt
    cached = applyProgressEvent(cached, { step: "failed", tag: "v3.4.0", error: "disk full" });

    expect(visibleApply(cached, superseded)?.error).toBe("disk full");
    // Holds even if the guard had not been dropped: nothing was inherited.
    expect(cached.started_at).toBeNull();
    expect(isSupersededApply(cached, previousFailure)).toBe(false);
  });
});

describe("(b) the same local rejection twice in a row", () => {
  it("shows the second rejection", () => {
    const first = rejectedApply("v3.4.0", "stale");
    let superseded: UpdateApplyState | null = isApplyTerminal(first) ? first : null;
    expect(seedAttempt(first, "v3.4.0", "manual", superseded)?.state).toBe("staging");

    superseded = null; // the catch path drops the guard before synthesising
    const second = rejectedApply("v3.4.0", "stale");
    expect(visibleApply(second, superseded)).toBe(second);
    // What the guard would have done had it stayed.
    expect(visibleApply(second, first)).toBeNull();
  });
});

describe("seedAttempt", () => {
  it("never overwrites something fresher than a placeholder", () => {
    const busy = { ...previousFailure, state: "staging" as const, finished_at: null };
    expect(seedAttempt(busy, "v3.4.0", "auto", null)).toBe(busy);
    const ownOutcome = { ...previousFailure, started_at: "2026-10-30T09:00:00+03:00" };
    expect(seedAttempt(ownOutcome, "v3.4.0", "auto", previousFailure)).toBe(ownOutcome);
  });
});

describe("autoPendingNext (3: a server-timer apply with no click)", () => {
  const pending = { tag: "v3.4.0", deadline: "2026-10-30T09:00:00+03:00", seconds_left: 0 };
  const status = (apply: UpdateApplyState, armed: boolean): Pick<UpdateStatusResult, "apply" | "auto"> => ({
    apply,
    auto: { enabled: true, pending: armed ? pending : null, blacklist: [] },
  });

  it("waits while the countdown is armed", () => {
    expect(autoPendingNext(status(previousFailure, true), previousFailure, 0)).toBe("stay");
  });

  it("waits out the settle gap instead of going idle", () => {
    expect(autoPendingNext(status(previousFailure, false), previousFailure, 0)).toBe("stay");
    expect(autoPendingNext(status(idle, false), idle, 2000)).toBe("stay");
  });

  it("lands in progress once staging is recorded", () => {
    const staging = { ...previousFailure, state: "staging" as const, started_at: "2026-10-30T09:00:00+03:00", finished_at: null };
    expect(autoPendingNext(status(staging, false), previousFailure, 2000)).toBe("progress");
  });

  it("shows an outcome the settled apply already reached", () => {
    const fresh = { ...previousFailure, started_at: "2026-10-30T09:00:00+03:00" };
    expect(autoPendingNext(status(fresh, false), previousFailure, 2000)).toBe("terminal");
  });

  it("reads a countdown gone with nothing new as a cancellation", () => {
    expect(autoPendingNext(status(previousFailure, false), previousFailure, AUTO_SETTLE_GRACE_MS)).toBe("idle");
    expect(autoPendingNext(status(previousFailure, false), null, 0)).toBe("idle");
  });
});

describe("isApplyBusy / isApplyTerminal", () => {
  it("classifies every state", () => {
    const states: UpdateApplyState["state"][] = ["idle", "staging", "switching", "done", "failed", "rolled_back"];
    const of = (state: UpdateApplyState["state"]) => ({ ...previousFailure, state });
    expect(states.filter((s) => isApplyBusy(of(s)))).toEqual(["staging", "switching"]);
    expect(states.filter((s) => isApplyTerminal(of(s)))).toEqual(["done", "failed", "rolled_back"]);
    expect(isApplyBusy(undefined)).toBe(false);
  });
});

describe("staleTargetRetryTag", () => {
  const latest = (tag: string | null, update_available: boolean) => ({
    latest_known: { tag, checked_at: null, update_available },
  });

  it("offers the release the server now knows about", () => {
    expect(staleTargetRetryTag(latest("v3.4.1", true), "v3.4.0")).toBe("v3.4.1");
  });

  it("never re-offers the tag the server just rejected", () => {
    expect(staleTargetRetryTag(latest("v3.4.1", true), "v3.4.1")).toBeNull();
  });

  it("offers nothing when there is nothing left to update to", () => {
    expect(staleTargetRetryTag(latest("v3.4.1", false), "v3.4.0")).toBeNull();
    expect(staleTargetRetryTag(latest(null, true), "v3.4.0")).toBeNull();
    expect(staleTargetRetryTag(undefined, "v3.4.0")).toBeNull();
  });
});
