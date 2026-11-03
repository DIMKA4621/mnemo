"use client";

import { useState } from "react";
import { Button, Input } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { usePatchBank } from "@/hooks/useMemoryMutations";
import { ModalShell } from "@/components/common/ModalShell";
import { ApiError } from "@/lib/api/fetcher";
import type { BankInfo } from "@/lib/api/memory";

interface RenameDialogProps {
  bank: BankInfo | null;
  onClose: () => void;
}

/** Id, root, token and index stay as they are; name-based refs (`--bank`) follow the new name. */
export function RenameDialog({ bank, onClose }: RenameDialogProps) {
  const t = useT();
  // Keyed by `bank?.id` in the parent, so the field starts at the current name.
  const [value, setValue] = useState(bank?.name ?? "");
  const patchMutation = usePatchBank();

  const trimmed = value.trim();
  const ready = !!bank && !patchMutation.isPending && trimmed !== "" && trimmed !== bank.name;
  const error = patchMutation.error;
  const errorText = !error
    ? null
    : error instanceof ApiError && error.code === "bank_exists"
      ? t("common.rename.taken")
      : error instanceof Error
        ? error.message
        : String(error);

  async function submit() {
    if (!bank || !ready) return;
    try {
      await patchMutation.mutateAsync({ bankId: bank.id, req: { name: trimmed } });
      onClose();
    } catch {
      // Shown inline below; the dialog stays open.
    }
  }

  return (
    <ModalShell
      open={!!bank}
      title={t("common.rename.title")}
      onClose={onClose}
      busy={patchMutation.isPending}
      footer={
        <>
          <Button disabled={patchMutation.isPending} onClick={onClose}>
            {t("common.btn.cancel")}
          </Button>
          <Button type="primary" disabled={!ready} loading={patchMutation.isPending} onClick={submit}>
            {t("common.rename.submit")}
          </Button>
        </>
      }
    >
      {bank && (
        <>
          <label className="fs-label" htmlFor="bank-rename-input">
            {t("common.rename.label")}
          </label>
          <Input
            id="bank-rename-input"
            spellCheck={false}
            autoComplete="off"
            value={value}
            status={errorText ? "error" : undefined}
            aria-invalid={errorText ? true : undefined}
            aria-describedby={errorText ? "bank-rename-hint bank-rename-error" : "bank-rename-hint"}
            disabled={patchMutation.isPending}
            onChange={(e) => {
              setValue(e.target.value);
              if (patchMutation.error) patchMutation.reset();
            }}
            onPressEnter={submit}
            autoFocus
          />
          <p className="fs-hint" id="bank-rename-hint">
            {t("common.rename.hint")}
          </p>
          {errorText && (
            <p className="modal-error" id="bank-rename-error" role="alert">
              {errorText}
            </p>
          )}
        </>
      )}
    </ModalShell>
  );
}
