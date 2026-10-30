"use client";

import { useT } from "@/lib/i18n/hooks";
import { StatusBadge } from "@/components/common/StatusBadge";
import { bankState, fmtBytes, fmtDateTime } from "@/lib/memory/format";
import type { BankInfo } from "@/lib/api/memory";
import type { BankCardData } from "@/hooks/useMemoryQueries";
import { BankMenu } from "./BankMenu";

function statusNoteKey(bank: BankCardData): string {
  if (bank.status === "indexing") {
    return bank.chunks > 0 ? "memory.statusNote.indexingHasChunks" : "memory.statusNote.indexingEmpty";
  }
  if (bank.status === "empty") {
    return bank.displayQueued > 0 ? "memory.statusNote.emptyQueued" : "memory.statusNote.emptyIdle";
  }
  return "memory.statusNote.ready";
}

interface BankCardProps {
  bank: BankCardData;
  selected: boolean;
  note: string | null;
  onSelect: (bankId: string) => void;
  onSync: (bank: BankInfo) => void;
  onRebuild: (bank: BankInfo) => void;
  onOpenToken: (bank: BankInfo) => void;
  onSetState: (bank: BankInfo, state: BankInfo["state"]) => void;
  onRemove: (bank: BankInfo) => void;
}

export function BankCard({ bank, selected, note, onSelect, onSync, onRebuild, onOpenToken, onSetState, onRemove }: BankCardProps) {
  const t = useT();
  const state = bankState(bank);
  const classes = ["bank"];
  if (selected) classes.push("is-selected");
  if (state === "disabled") classes.push("is-disabled");

  return (
    <div className={classes.join(" ")} data-bank={bank.id} onClick={() => onSelect(bank.id)}>
      <div className="bank-row">
        <div className="bank-head">
          <span className="bank-name" title={`id: ${bank.id}`}>{bank.name}</span>
          <StatusBadge
            variant={bank.status}
            text={t(`common.status.${bank.status}`)}
            title={t(statusNoteKey(bank))}
          />
          {bank.git ? (
            <StatusBadge variant="git" text="git" />
          ) : (
            <StatusBadge variant="nogit" text="no git" />
          )}
          {state === "frozen" && (
            <StatusBadge
              variant="frozen"
              text={t("memory.bank.frozenBadge")}
              title={t("memory.bank.frozenBadgeTitle", { date: fmtDateTime(bank.last_indexed) })}
            />
          )}
          {state === "disabled" && (
            <StatusBadge variant="off" text={t("memory.bank.disabledBadge")} />
          )}
          {bank.exists === false && (
            <StatusBadge variant="off" text={t("memory.bank.noRootBadge")} />
          )}
        </div>
        <BankMenu
          bank={bank}
          onSync={onSync}
          onRebuild={onRebuild}
          onOpenToken={onOpenToken}
          onSetState={onSetState}
          onRemove={onRemove}
        />
      </div>
      <span className="bank-root">{bank.root}</span>
      <div className="bank-stats">
        <span>{t("memory.bank.filesStat", { n: bank.files })}</span>
        <span>{t("memory.bank.chunksStat", { n: bank.chunks })}</span>
        {bank.displayQueued > 0 && <span>{t("memory.bank.queuedStat", { n: bank.displayQueued })}</span>}
        <span title={t("memory.bank.dbSizeTitle")}>{fmtBytes(bank.db_bytes)}</span>
      </div>
      <div className="bank-stats">
        <span className="mnemo-muted">{t("memory.bank.lastIndexed", { date: fmtDateTime(bank.last_indexed) })}</span>
      </div>
      {bank.status !== "ready" && (
        <div className="bank-stats">
          <span className={bank.status === "indexing" ? "note-live" : "mnemo-muted"}>
            {t(statusNoteKey(bank))}
          </span>
        </div>
      )}
      {note && <div className="progress-text">{note}</div>}
      {bank.last_error && <div className="bank-error">{bank.last_error}</div>}
    </div>
  );
}
