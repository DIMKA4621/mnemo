"use client";

import { useState } from "react";
import { useT } from "@/lib/i18n/hooks";
import { useBankCards, type BankCardData } from "@/hooks/useMemoryQueries";
import { usePatchBank, useReindex } from "@/hooks/useMemoryMutations";
import { bankState } from "@/lib/memory/format";
import type { BankInfo } from "@/lib/api/memory";
import { BankCard } from "./BankCard";

interface BankListProps {
  selectedBankId: string | null;
  onSelect: (bankId: string) => void;
  onOpenToken: (bank: BankInfo) => void;
  onOpenRemoval: (bank: BankInfo) => void;
}

export function BankList({ selectedBankId, onSelect, onOpenToken, onOpenRemoval }: BankListProps) {
  const t = useT();
  const banksQuery = useBankCards();
  const reindexMutation = useReindex();
  const patchMutation = usePatchBank();
  const [notes, setNotes] = useState<Map<string, string>>(new Map());

  function setNote(bankId: string, text: string) {
    setNotes((prev) => {
      const next = new Map(prev);
      next.set(bankId, text);
      return next;
    });
    setTimeout(() => {
      setNotes((prev) => {
        if (prev.get(bankId) !== text) return prev;
        const next = new Map(prev);
        next.delete(bankId);
        return next;
      });
    }, 6000);
  }

  function handleReindex(bank: BankInfo, full: boolean) {
    reindexMutation.mutate(
      { bank: bank.id, full },
      {
        onSuccess: (res) =>
          setNote(
            bank.id,
            t("common.reindex.queuedNote", {
              what: t(full ? "common.taskKind.rebuild" : "common.taskKind.bulk"),
              n: res.queued,
              ids: res.task_ids.join(", "),
            }),
          ),
        onError: (err) => setNote(bank.id, err instanceof Error ? err.message : String(err)),
      },
    );
  }

  function handleSetState(bank: BankInfo, next: BankInfo["state"]) {
    if (bankState(bank) === next) return;
    patchMutation.mutate(
      { bankId: bank.id, req: { state: next } },
      {
        onSuccess: (info) =>
          setNote(bank.id, t("common.bankMenu.stateNote", { state: t(`memory.bankState.${info.state}.label`).toLowerCase() })),
        onError: (err) => setNote(bank.id, err instanceof Error ? err.message : String(err)),
      },
    );
  }

  const banks: BankCardData[] = banksQuery.data ?? [];

  if (banksQuery.isLoading) {
    return <p className="empty-hint">{t("memory.tree.loading")}</p>;
  }

  if (banks.length === 0) {
    return <p className="empty-hint">{t("memory.banks.emptyHint")}</p>;
  }

  return (
    <>
      {banks.map((bank) => (
        <BankCard
          key={bank.id}
          bank={bank}
          selected={bank.id === selectedBankId}
          note={notes.get(bank.id) ?? null}
          onSelect={onSelect}
          onSync={(b) => handleReindex(b, false)}
          onRebuild={(b) => handleReindex(b, true)}
          onOpenToken={onOpenToken}
          onSetState={handleSetState}
          onRemove={onOpenRemoval}
        />
      ))}
    </>
  );
}
