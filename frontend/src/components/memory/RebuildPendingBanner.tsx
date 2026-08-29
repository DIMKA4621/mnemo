"use client";

import { useState } from "react";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useBankCards, type BankCardData } from "@/hooks/useMemoryQueries";
import { useReindex } from "@/hooks/useMemoryMutations";
import { bankState, fmtBytes } from "@/lib/memory/format";
import { ModalShell } from "@/components/common/ModalShell";

function pendingGroups(banks: BankCardData[]) {
  const pending = banks.filter((b) => b.rebuild_pending);
  return {
    actionable: pending.filter((b) => bankState(b) !== "disabled" && b.status !== "indexing" && !b.displayIndexing),
    running: pending.filter((b) => bankState(b) !== "disabled" && (b.status === "indexing" || b.displayIndexing)),
    disabled: pending.filter((b) => bankState(b) === "disabled"),
  };
}

export function RebuildPendingBanner() {
  const t = useT();
  const banksQuery = useBankCards();
  const reindexMutation = useReindex();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  const banks = banksQuery.data ?? [];
  const groups = pendingGroups(banks);
  const total = groups.actionable.length + groups.running.length + groups.disabled.length;

  if (total === 0) return null;

  const parts: string[] = [];
  if (groups.actionable.length) parts.push(t("memory.rebuild.notice.actionable", { n: groups.actionable.length }));
  if (groups.running.length) parts.push(t("memory.rebuild.notice.running", { n: groups.running.length }));
  if (groups.disabled.length) parts.push(t("memory.rebuild.notice.disabled", { n: groups.disabled.length }));
  const text = parts.join(" · ") + t("memory.rebuild.notice.suffix");

  async function submit() {
    const targets = groups.actionable;
    if (targets.length === 0 || busy) return;
    setBusy(true);
    setErrorText(null);
    const outcomes = await Promise.allSettled(
      targets.map((bank) => reindexMutation.mutateAsync({ bank: bank.id, full: true })),
    );
    const failed: string[] = [];
    outcomes.forEach((outcome, i) => {
      if (outcome.status === "rejected") {
        const err = outcome.reason;
        failed.push(`${targets[i].name}: ${err instanceof Error ? err.message : String(err)}`);
      }
    });
    setBusy(false);
    if (failed.length === 0) {
      setOpen(false);
      return;
    }
    setErrorText(failed.join(" · "));
  }

  return (
    <>
      <div className="rebuild-banner" role="status">
        <span className="rebuild-banner-text">{text}</span>
        {groups.actionable.length > 0 && (
          <Button size="small" disabled={busy} onClick={() => setOpen(true)}>
            {t("memory.rebuild.action")}
          </Button>
        )}
      </div>
      <ModalShell
        open={open}
        title={t("memory.rebuild.dialogTitle")}
        ariaLabel={t("memory.rebuild.dialogAriaLabel")}
        onClose={() => setOpen(false)}
        busy={busy}
        footer={
          <>
            <Button disabled={busy} onClick={() => setOpen(false)}>
              {t("common.btn.cancel")}
            </Button>
            <Button type="primary" loading={busy} disabled={groups.actionable.length === 0} onClick={submit}>
              {busy ? t("memory.rebuild.queuing") : t("memory.rebuild.action")}
            </Button>
          </>
        }
      >
        <p>{t("memory.rebuild.dialog.lead", { n: groups.actionable.length })}</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, margin: "8px 0" }}>
          {groups.actionable.map((bank) => (
            <div key={bank.id} style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--mono)", fontSize: 11 }}>
              <span>{bank.name}</span>
              <span>{t("memory.rebuild.dialog.chunksLabel", { n: bank.chunks })} · {fmtBytes(bank.db_bytes)}</span>
            </div>
          ))}
        </div>
        <p style={{ color: "var(--fg-mute)", fontSize: 12 }}>{t("memory.rebuild.dialog.note")}</p>
        {errorText && <p style={{ color: "var(--err)", fontSize: 12 }}>{errorText}</p>}
      </ModalShell>
    </>
  );
}
