"use client";

import { useState } from "react";
import { Button, Input } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useBankMcpWiring } from "@/hooks/useMemoryQueries";
import { useRemoveBank } from "@/hooks/useMemoryMutations";
import { ModalShell } from "@/components/common/ModalShell";
import { fmtBytes } from "@/lib/memory/format";
import { projectRootForBankPath } from "@/lib/memory/bankRoot";
import type { BankInfo } from "@/lib/api/memory";

interface RemovalDialogProps {
  bank: BankInfo | null;
  onClose: () => void;
  onRemoved: (bankId: string) => void;
}

/**
 * The only irreversible action in the console — what makes it irreversible
 * is not the index (that rebuilds) but the token: a removed bank's token is
 * minted at random and cannot be recreated, so every `.mcp.json` addressing
 * it stops working for good. Hence the type-the-name confirmation, and the
 * dialog leading with the token rather than the megabytes.
 */
export function RemovalDialog({ bank, onClose, onRemoved }: RemovalDialogProps) {
  const t = useT();
  const open = !!bank;
  // The parent keys this component by `bank?.id` (`app/memory/page.tsx`),
  // so opening it for a different bank remounts it fresh — `dropIndex`/
  // `typed` start at their real initial values with no reset effect needed.
  const [dropIndex, setDropIndex] = useState(true);
  const [stripMcp, setStripMcp] = useState(false);
  const [typed, setTyped] = useState("");

  const projectRoot = projectRootForBankPath(bank?.root);
  const wiringQuery = useBankMcpWiring(bank?.id ?? null, open && projectRoot !== null);
  const removeMutation = useRemoveBank();

  // "Adjust state when a value changes" during render (React's own
  // documented pattern), not a `useEffect`: default `stripMcp` to whatever
  // the wiring lookup says the moment it resolves, without overriding a
  // manual toggle on every later render of the same result.
  const [appliedWiringDefault, setAppliedWiringDefault] = useState(false);
  if (!appliedWiringDefault && wiringQuery.data) {
    setAppliedWiringDefault(true);
    setStripMcp(wiringQuery.data.has_wiring);
  }
  const hasWiring = !!wiringQuery.data?.has_wiring;

  const ready = !removeMutation.isPending && !!bank && typed.trim() === bank.name;

  async function submit() {
    if (!bank || !ready) return;
    try {
      const result = await removeMutation.mutateAsync({ bankId: bank.id, dropIndex, stripMcp });
      if (stripMcp && result.mcp_stripped) {
        // No toast mechanism for a removal that just closed its own dialog —
        // recorded for anyone checking what actually got touched.
        console.info("mnemo: MCP wiring stripped from", result.mcp_stripped.join(", ") || "(nothing to strip)");
      }
      onRemoved(bank.id);
    } catch {
      // The error surfaces via `removeMutation.error` below; dialog stays open.
    }
  }

  return (
    <ModalShell
      open={open}
      title={t("common.removal.title")}
      ariaLabel={t("common.removal.ariaLabel")}
      onClose={onClose}
      busy={removeMutation.isPending}
      footer={
        <>
          <Button disabled={removeMutation.isPending} onClick={onClose}>
            {t("common.btn.cancel")}
          </Button>
          <Button danger disabled={!ready} loading={removeMutation.isPending} onClick={submit}>
            {removeMutation.isPending ? t("common.removal.busy") : t("common.removal.submit")}
          </Button>
        </>
      }
    >
      {bank && (
        <>
          <p className="rm-lead">
            {t("common.removal.leadPrefix")}
            <strong>{bank.name}</strong>
            {t("common.removal.leadSuffix")}
          </p>

          <dl className="rm-effects">
            <dt className="is-loss">{t("common.removal.goneForever")}</dt>
            <dd>{t("common.removal.goneForeverText")}</dd>
            <dt className="is-safe">{t("common.removal.untouched")}</dt>
            <dd>
              {t("common.removal.untouchedPrefix")}
              <code>{bank.root}</code>
              {t("common.removal.untouchedSuffix")}
            </dd>
          </dl>

          <label className="rm-check">
            <input
              type="checkbox"
              checked={dropIndex}
              disabled={removeMutation.isPending}
              onChange={(e) => setDropIndex(e.target.checked)}
            />
            <span>{t("common.removal.dropIndex", { bytes: fmtBytes(bank.db_bytes) })}</span>
          </label>

          {projectRoot && (
            <>
              <label className="rm-check">
                <input
                  type="checkbox"
                  checked={stripMcp}
                  disabled={removeMutation.isPending || !hasWiring}
                  onChange={(e) => setStripMcp(e.target.checked)}
                />
                <span>
                  {t("common.removal.stripMcpPrefix")}
                  <code>{projectRoot}</code>)
                </span>
              </label>
              {!hasWiring && !wiringQuery.isLoading && (
                <p className="fs-hint fs-warn">{t("common.removal.noMcpJson")}</p>
              )}
            </>
          )}

          <label className="fs-label">{t("common.removal.confirmLabel")}</label>
          <Input
            spellCheck={false}
            autoComplete="off"
            placeholder={bank.name}
            value={typed}
            disabled={removeMutation.isPending}
            onChange={(e) => setTyped(e.target.value)}
            onPressEnter={submit}
            autoFocus
          />

          {removeMutation.error && (
            <p className="modal-error">
              {removeMutation.error instanceof Error ? removeMutation.error.message : String(removeMutation.error)}
            </p>
          )}
        </>
      )}
    </ModalShell>
  );
}
