"use client";

import { useState } from "react";
import { Button, Input } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useBankToken, useStatus } from "@/hooks/useMemoryQueries";
import { useRegenerateToken } from "@/hooks/useMemoryMutations";
import { ModalShell } from "@/components/common/ModalShell";
import { McpModeSwitch, McpSnippets, TemplateNotes, TokenRow, tokenShown, type McpMode } from "@/components/common/McpAccess";
import {
  DEFAULT_INSTANCE,
  defaultEntryName,
  mcpDocument,
  sanitizeEntryName,
  sedLineText,
  tokenVar,
} from "@/lib/memory/tokenSnippets";
import type { BankInfo } from "@/lib/api/memory";

interface BankTokenPanelProps {
  bank: BankInfo | null;
  onClose: () => void;
}

export function BankTokenPanel({ bank, onClose }: BankTokenPanelProps) {
  const t = useT();
  const open = !!bank;
  // The parent keys this component by `bank?.id` (`app/memory/page.tsx`),
  // so opening it for a different bank remounts it fresh — these all start
  // at their real initial values instead of needing a reset effect.
  const [revealed, setRevealed] = useState(false);
  const [scope, setScope] = useState<McpMode>("literal");
  const [entry, setEntry] = useState(() => (bank ? defaultEntryName(bank.name) : DEFAULT_INSTANCE));
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const tokenQuery = useBankToken(bank?.id ?? null, open);
  const statusQuery = useStatus();
  const regenMutation = useRegenerateToken();

  const value = tokenQuery.data?.token ?? null;
  const ready = value != null;
  const shown = tokenShown(value, revealed);

  const host = statusQuery.data?.service.host || (typeof window !== "undefined" ? window.location.hostname : "127.0.0.1");
  const port = statusQuery.data?.service.port ?? (typeof window !== "undefined" ? Number(window.location.port) || 80 : 80);

  function handleEntryChange(next: string) {
    setEntry(sanitizeEntryName(next));
  }

  const varName = tokenVar(entry);
  const entryHint =
    t("common.token.entryHint.base", { entry }) +
    (varName !== "MNEMO_TOKEN" ? t("common.token.entryHint.own", { var: varName }) : "");

  const snippets =
    scope === "literal"
      ? [
          {
            caption: t("common.token.caption.literal"),
            build: (tok: string) => mcpDocument(entry, `http://${host}:${port}/mcp?token=${tok}`),
          },
        ]
      : [
          {
            caption: t("common.token.caption.template"),
            build: () => mcpDocument(entry, `http://{{MNEMO_HOST}}:{{MNEMO_PORT}}/mcp?token={{${varName}}}`),
          },
          {
            caption: t("common.token.caption.env"),
            build: (tok: string) => `MNEMO_HOST=${host}\nMNEMO_PORT=${port}\n${varName}=${tok}`,
          },
        ];

  async function regenerate() {
    if (!bank) return;
    try {
      await regenMutation.mutateAsync(bank.id);
      setRevealed(false);
      setConfirming(false);
      setNote(t("common.token.regeneratedNote"));
    } catch {
      // The error surfaces via `regenMutation.error` below.
    }
  }

  return (
    <ModalShell
      open={open}
      title={bank ? t("common.token.titleFor", { name: bank.name }) : t("common.token.title")}
      ariaLabel={t("common.token.ariaLabel")}
      onClose={onClose}
      wide
      footer={
        <>
          <Button
            style={{ marginRight: "auto" }}
            title={t("common.token.regenTitle")}
            disabled={regenMutation.isPending || confirming || !ready}
            onClick={() => setConfirming(true)}
          >
            {t("common.token.regen")}
          </Button>
          <Button onClick={onClose}>{t("common.btn.close")}</Button>
        </>
      }
    >
      {bank && (
        <>
          <TokenRow label={t("common.token.bankTokenLabel")} value={value} revealed={revealed} onRevealedChange={setRevealed} />
          <p className="tok-note">{t("common.token.scopeNote", { name: bank.name })}</p>

          <label className="fs-label">{t("common.token.entryLabel")}</label>
          <Input value={entry} spellCheck={false} onChange={(e) => handleEntryChange(e.target.value)} />
          <p className="tok-note">{entryHint}</p>

          <McpModeSwitch value={scope} onChange={setScope} />

          {scope === "template" && (
            <p className="tok-lead">
              {t("common.token.templateLead.part1")}
              <code>mnemo init</code>
              {t("common.token.templateLead.part2")}
              <code>cp .mcp.env.example .mcp.env</code>
              {t("common.token.templateLead.part3")}
              <code>bash mcp-setup.sh</code>
              {t("common.token.templateLead.part4")}
            </p>
          )}

          <McpSnippets snippets={snippets} token={value} shown={shown} />

          {scope === "template" && <TemplateNotes sedLine={sedLineText(entry)} />}

          {confirming && (
            <div className="tok-confirm">
              <p className="tok-confirm-text">{t("common.token.regenConfirm", { name: bank.name })}</p>
              <div className="tok-confirm-row">
                <Button size="small" onClick={() => setConfirming(false)}>
                  {t("common.btn.cancel")}
                </Button>
                <Button size="small" type="primary" danger loading={regenMutation.isPending} onClick={regenerate}>
                  {t("common.token.regenYes")}
                </Button>
              </div>
            </div>
          )}

          {note && <p className="tok-ok">{note}</p>}
          {(tokenQuery.error || regenMutation.error) && (
            <p className="modal-error">
              {(tokenQuery.error ?? regenMutation.error) instanceof Error
                ? (tokenQuery.error ?? regenMutation.error)!.message
                : String(tokenQuery.error ?? regenMutation.error)}
            </p>
          )}
        </>
      )}
    </ModalShell>
  );
}
