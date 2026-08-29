"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { ModalShell } from "@/components/common/ModalShell";
import { useUpdateStatus, useUpdateProgressPolling, useAutoPendingPolling } from "@/hooks/useSettingsQueries";
import { useApplyUpdate, useConfirmAutoPending, useCancelAutoPending } from "@/hooks/useSettingsMutations";
import { useUpdateModalStore } from "@/lib/store/update-modal";
import { queryKeys } from "@/lib/query/keys";
import { ApiError } from "@/lib/api/fetcher";
import type { UpdateApplyState, UpdateStatusResult } from "@/lib/api/settings";
import "./settings.css";

const UPDATE_POLL_TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes of silence before giving up

function updateStepIndex(apply: UpdateApplyState): number {
  if (apply.state === "switching") return 2;
  if (apply.step === "venv") return 1;
  return 0; // 'download', or staging with no step reported yet
}

const UPDATE_STEPS = [
  { key: "download", labelKey: "update.steps.download" },
  { key: "venv", labelKey: "update.steps.venv" },
  { key: "switching", labelKey: "update.steps.switching" },
] as const;

/** A countdown ticking display, seeded to a plain literal and advanced only
 *  from inside the interval's own callback — never a raw `Date.now()` call
 *  during render, and never a `setState` call synchronous in the effect
 *  body itself, both of which the purity/set-state-in-effect lint rules
 *  reject. Mounting (not an internal effect keyed on `deadline`) is what
 *  resets the display if the deadline itself ever changes. */
function AutoPendingCountdown({ deadline }: { deadline: string }) {
  const [secondsLeft, setSecondsLeft] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => {
      setSecondsLeft(Math.max(0, Math.round((Date.parse(deadline) - Date.now()) / 1000)));
    }, 1000);
    return () => clearInterval(timer);
  }, [deadline]);
  return <strong className="upd-countdown">{secondsLeft}</strong>;
}

/** Same discipline as `AutoPendingCountdown` above, counting down from a
 *  fixed starting point instead of towards a server-given deadline —
 *  mounted only while `autoCloseActive`, so mounting IS the reset to 10. */
function TerminalAutoClose({ seconds, onDone }: { seconds: number; onDone: () => void }) {
  const [left, setLeft] = useState(seconds);
  useEffect(() => {
    const timer = setInterval(() => {
      setLeft((s) => {
        if (s <= 1) {
          onDone();
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return <strong className="upd-countdown">{left}</strong>;
}

/**
 * Self-update: `confirm -> progress -> terminal` (manual) or
 * `auto-pending -> progress -> terminal` (unattended), with `timeout` as an
 * escape hatch out of `progress`. Mounted once at shell level
 * (`AppShell.tsx`) — a countdown is not tied to being on `/settings`.
 *
 * Design + the one hard trap this is built around:
 * `.claude/memory/topics/engine-self-update-design.md`, and the vanilla
 * console's `update.js` (ported closely). Once staging finishes, the API
 * process hands off to a detached process that STOPS it to switch versions
 * — the WS channel dies with it, by design, so `GET /api/update/status`
 * polling (`useUpdateProgressPolling`, 2s, active only while this phase is
 * `'progress'`) is the actual source of truth for the outcome, not a WS
 * disconnect. `lib/ws/dispatch.ts`'s `update_progress`/`update_auto_pending`
 * handlers patch the same query cache this component reads, so a poll tick
 * and a WS event are indistinguishable to the state machine below.
 */
export function UpdateModal() {
  const t = useT();
  const qc = useQueryClient();
  const phase = useUpdateModalStore((s) => s.phase);
  const everSwitching = useUpdateModalStore((s) => s.everSwitching);
  const autoPendingError = useUpdateModalStore((s) => s.autoPendingError);
  const setPhase = useUpdateModalStore((s) => s.setPhase);
  const setEverSwitching = useUpdateModalStore((s) => s.setEverSwitching);
  const setAutoPendingError = useUpdateModalStore((s) => s.setAutoPendingError);

  const statusQuery = useUpdateStatus();
  useUpdateProgressPolling(phase === "progress");
  useAutoPendingPolling(phase === "auto-pending");
  const applyMutation = useApplyUpdate();
  const confirmAutoMutation = useConfirmAutoPending();
  const cancelAutoMutation = useCancelAutoPending();

  const bootstrappedRef = useRef(false);
  const lastProgressAtRef = useRef(0);
  const lastSeenRef = useRef<{ step: string | null; detail: string | null }>({ step: null, detail: null });

  // Resume an apply already in flight (a reload, or another tab/CLI
  // triggered it), or a countdown already ticking, the moment the first
  // status fetch resolves — mirrors the vanilla console's
  // `refreshUpdateStatus()`, run once from `boot()`.
  useEffect(() => {
    if (bootstrappedRef.current || !statusQuery.data) return;
    bootstrappedRef.current = true;
    const apply = statusQuery.data.apply;
    if (apply.state === "staging" || apply.state === "switching") {
      setEverSwitching(apply.state === "switching");
      setPhase("progress");
      return;
    }
    if (statusQuery.data.auto.pending) setPhase("auto-pending");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusQuery.data]);

  // Reset the stall clock on every fresh entry into `'progress'` (including
  // a retry out of `'timeout'`).
  useEffect(() => {
    if (phase === "progress") {
      lastProgressAtRef.current = Date.now();
      lastSeenRef.current = { step: null, detail: null };
    }
  }, [phase]);

  // The progress phase's actual state machine — keyed on
  // `dataUpdatedAt`/`errorUpdatedAt` rather than `data` itself, so a poll
  // tick that returns byte-identical JSON (structural sharing keeps the
  // object reference stable) still re-runs this check, the same as the
  // vanilla console's `pollUpdateStatusOnce` running its full body on every
  // tick regardless of whether anything changed.
  useEffect(() => {
    if (phase !== "progress") return;
    const apply = qc.getQueryData<UpdateStatusResult>(queryKeys.updateStatus.all)?.apply;
    if (apply) {
      if (apply.state === "switching") setEverSwitching(true);
      if (apply.step !== lastSeenRef.current.step || (apply.detail ?? null) !== lastSeenRef.current.detail) {
        lastSeenRef.current = { step: apply.step, detail: apply.detail ?? null };
        lastProgressAtRef.current = Date.now();
      }
      if (apply.state === "done" || apply.state === "failed" || apply.state === "rolled_back") {
        setPhase("terminal");
        return;
      }
    }
    if (Date.now() - lastProgressAtRef.current >= UPDATE_POLL_TIMEOUT_MS) setPhase("timeout");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusQuery.dataUpdatedAt, statusQuery.errorUpdatedAt, phase]);

  // The re-sync half of auto-pending: settlement/cancellation that happened
  // without this tab doing anything (another tab, the CLI, or the timer
  // firing server-side) — same "poll is the truth" discipline as the
  // progress phase's own effect above.
  useEffect(() => {
    if (phase !== "auto-pending") return;
    const data = qc.getQueryData<UpdateStatusResult>(queryKeys.updateStatus.all);
    if (!data) return;
    if (data.apply.state === "staging" || data.apply.state === "switching") {
      setEverSwitching(data.apply.state === "switching");
      setPhase("progress");
      return;
    }
    if (!data.auto.pending) setPhase("idle");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusQuery.dataUpdatedAt, phase]);

  const apply = statusQuery.data?.apply ?? null;

  // Terminal phase's auto-close countdown — auto-triggered AND successful
  // only: a failed/rolled-back outcome needs a human's attention regardless
  // of trigger, and a manual click already means someone is watching this
  // screen right now.
  const autoCloseActive = phase === "terminal" && apply?.state === "done" && apply?.trigger === "auto";

  // Every "dismiss this modal" path (✕, backdrop, Escape, the terminal
  // screen's own button/countdown) funnels through here. A successful apply
  // means the running backend genuinely changed and this page's own JS/CSS
  // is stale — the only safe way to stop running it is a real reload; any
  // other outcome keeps the plain dismiss.
  function dismiss() {
    if (phase === "terminal" && apply?.state === "done") {
      window.location.reload();
      return;
    }
    setPhase("idle");
    setAutoPendingError(null);
  }

  async function confirmApply() {
    const tag = statusQuery.data?.latest_known.tag;
    if (!tag) return;
    setEverSwitching(false);
    setPhase("progress");
    try {
      await applyMutation.mutateAsync(tag);
    } catch (err) {
      // Rejected before anything started (stale target, one already
      // running) — nothing was touched. Reported the same way a later
      // failure is, just without ever having left "confirm".
      let message = err instanceof Error ? err.message : String(err);
      if (err instanceof ApiError && err.code === "stale_target") {
        const latestTag = err.detail?.latest_tag;
        if (typeof latestTag === "string") message = t("update.confirm.staleTarget", { tag: latestTag });
      }
      qc.setQueryData<UpdateStatusResult>(queryKeys.updateStatus.all, (old) =>
        old
          ? {
              ...old,
              apply: {
                state: "failed", tag, step: null, detail: null, error: message,
                started_at: null, finished_at: null, trigger: "manual",
              },
            }
          : old,
      );
      setPhase("terminal");
    }
  }

  async function confirmAuto() {
    try {
      await confirmAutoMutation.mutateAsync();
      setEverSwitching(false);
      setPhase("progress");
    } catch (err) {
      if (err instanceof ApiError && err.code === "auto_not_pending") {
        await statusQuery.refetch(); // race: settled or cancelled elsewhere
        return;
      }
      setAutoPendingError(err instanceof Error ? err.message : String(err));
    }
  }

  async function cancelAuto() {
    try {
      await cancelAutoMutation.mutateAsync();
      setPhase("idle");
    } catch (err) {
      if (err instanceof ApiError && err.code === "auto_not_pending") {
        await statusQuery.refetch();
        return;
      }
      setAutoPendingError(err instanceof Error ? err.message : String(err));
    }
  }

  if (phase === "idle") return null;

  const u = statusQuery.data ?? null;
  let body: ReactNode = null;
  let footer: ReactNode = null;

  if (phase === "confirm") {
    const currentTag = u?.current.tag ?? null;
    const latestTag = u?.latest_known.tag ?? null;
    body = (
      <>
        <p className="upd-row">
          {t("update.confirm.currentLabel")} <code>{currentTag || "—"}</code>
        </p>
        <p className="upd-row">
          {t("update.confirm.newLabel")} <strong>{latestTag || "—"}</strong>
        </p>
        <div className="tok-confirm">
          <p className="tok-confirm-text">{t("update.confirm.warning")}</p>
        </div>
      </>
    );
    footer = (
      <>
        <Button onClick={() => setPhase("idle")}>{t("common.btn.cancel")}</Button>
        <Button type="primary" disabled={!latestTag} onClick={confirmApply}>
          {t("update.confirm.okBtn")}
        </Button>
      </>
    );
  } else if (phase === "auto-pending") {
    const pending = u?.auto.pending ?? null;
    const tag = pending?.tag ?? u?.latest_known.tag ?? "—";
    body = (
      <>
        <p className="upd-row">
          {t("update.autoPending.leadPrefix")} <strong>{tag}</strong> {t("update.autoPending.leadMiddle")}
          {pending ? <AutoPendingCountdown deadline={pending.deadline} /> : <strong className="upd-countdown">0</strong>}{" "}
          {t("update.autoPending.leadSuffix")}
        </p>
        <div className="tok-confirm">
          <p className="tok-confirm-text">{t("update.autoPending.note")}</p>
        </div>
        {autoPendingError && <p className="upd-row modal-error">{autoPendingError}</p>}
      </>
    );
    footer = (
      <>
        <Button loading={cancelAutoMutation.isPending} onClick={cancelAuto}>
          {t("common.btn.cancel")}
        </Button>
        <Button type="primary" loading={confirmAutoMutation.isPending} onClick={confirmAuto}>
          {t("update.confirm.okBtn")}
        </Button>
      </>
    );
  } else if (phase === "progress") {
    const tag = apply?.tag || u?.latest_known.tag || "—";
    const activeIndex = apply ? updateStepIndex(apply) : 0;
    body = (
      <>
        <p className="upd-row">{t("update.progress.title", { tag })}</p>
        <div className="upd-steps">
          {UPDATE_STEPS.map((step, i) => {
            const status = i < activeIndex ? "done" : i === activeIndex ? "active" : "pending";
            return (
              <div key={step.key} className={`upd-step is-${status}`}>
                <span className="upd-step-mark">{status === "done" ? "✓" : i + 1}</span>
                <span className="upd-step-label">{t(step.labelKey)}</span>
                {status === "active" && (
                  <div className="upd-step-bar">
                    <i />
                  </div>
                )}
              </div>
            );
          })}
        </div>
        {apply?.step === "venv" && apply.detail && <p className="upd-note">{apply.detail}</p>}
        {everSwitching && <p className="upd-note">{t("update.progress.switchingNote")}</p>}
      </>
    );
    footer = null; // no buttons while this is running (nothing to press)
  } else if (phase === "timeout") {
    body = <p className="upd-row modal-error">{t("update.timeout.text")}</p>;
    footer = (
      <>
        <Button onClick={() => setPhase("progress")}>{t("update.timeout.retryBtn")}</Button>
        <Button type="primary" onClick={() => setPhase("idle")}>
          {t("common.btn.close")}
        </Button>
      </>
    );
  } else if (phase === "terminal") {
    const currentTag = u?.current.tag ?? null;
    let cls = "tok-ok";
    let text = "";
    if (apply?.state === "done") {
      text = t("update.terminal.done", { tag: currentTag || apply.tag || "—" });
    } else if (apply?.state === "rolled_back") {
      // Design point 5, verbatim: "problem, rolled back" instead of a
      // silent failure or a generic error.
      cls = "modal-error";
      text =
        t("update.terminal.rolledBack", { tag: apply.tag || "—", current: currentTag || "—" }) +
        (apply.error ? t("update.terminal.errorSuffix", { error: apply.error }) : "");
    } else {
      cls = "modal-error";
      text =
        t("update.terminal.failedBase") +
        (apply?.error ? t("update.terminal.failedWithError", { error: apply.error }) : t("update.terminal.failedNoError"));
      // Reaching 'switching' locally is what distinguishes "staging failed,
      // service untouched" from "the switch itself was attempted" — the
      // safer, narrower claim either way.
      text += everSwitching ? t("update.terminal.unknownState") : t("update.terminal.unchanged");
    }
    body = (
      <>
        <p className={`upd-row ${cls}`}>{text}</p>
        {autoCloseActive && (
          <p className="upd-row upd-terminal-autoclose">
            {t("update.terminal.autoClosePrefix")} <TerminalAutoClose seconds={10} onDone={dismiss} />{" "}
            {t("update.terminal.autoCloseSuffix")}
          </p>
        )}
      </>
    );
    footer = (
      <Button type="primary" onClick={dismiss}>
        {t("common.btn.close")}
      </Button>
    );
  }

  return (
    <ModalShell open title={t("update.modal.title")} onClose={dismiss} busy={phase === "progress"} footer={footer}>
      {body}
    </ModalShell>
  );
}
