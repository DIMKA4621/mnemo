"use client";

import { useState } from "react";
import { Button, Input, Segmented } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useBankToken, useStatus } from "@/hooks/useMemoryQueries";
import { useRegenerateToken } from "@/hooks/useMemoryMutations";
import { ModalShell } from "@/components/common/ModalShell";
import {
  DEFAULT_INSTANCE,
  copyText,
  defaultEntryName,
  mcpDocument,
  sanitizeEntryName,
  sedLineText,
  tokenVar,
} from "@/lib/memory/tokenSnippets";
import type { BankInfo } from "@/lib/api/memory";

function maskToken(value: string): string {
  return "•".repeat(value.length);
}

function CopyButton({ getText, title }: { getText: () => string | null; title: string }) {
  const t = useT();
  const [label, setLabel] = useState<string | null>(null);

  async function handleClick() {
    const text = getText();
    if (text == null) return;
    const ok = await copyText(text);
    if (!ok) {
      setLabel(t("common.token.copyFailed"));
      setTimeout(() => setLabel(null), 2000);
      return;
    }
    setLabel(t("common.btn.copied"));
    setTimeout(() => setLabel(null), 1400);
  }

  return (
    <Button size="small" title={title} disabled={label === t("common.token.copyFailed")} onClick={handleClick}>
      {label ?? t("common.btn.copy")}
    </Button>
  );
}

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
  const [scope, setScope] = useState<"literal" | "template">("literal");
  const [entry, setEntry] = useState(() => (bank ? defaultEntryName(bank.name) : DEFAULT_INSTANCE));
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const tokenQuery = useBankToken(bank?.id ?? null, open);
  const statusQuery = useStatus();
  const regenMutation = useRegenerateToken();

  const value = tokenQuery.data?.token ?? null;
  const ready = value != null;
  const shown = value == null ? "…" : revealed ? value : maskToken(value);

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
            secret: true,
            build: (tok: string) => mcpDocument(entry, `http://${host}:${port}/mcp?token=${tok}`),
          },
        ]
      : [
          {
            caption: t("common.token.caption.template"),
            secret: false,
            build: () => mcpDocument(entry, `http://{{MNEMO_HOST}}:{{MNEMO_PORT}}/mcp?token={{${varName}}}`),
          },
          {
            caption: t("common.token.caption.env"),
            secret: true,
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
          <label className="fs-label">{t("common.token.bankTokenLabel")}</label>
          <div className="tok-row">
            <Input className="tok-value" readOnly value={shown} disabled={!ready} />
            <Button
              disabled={!ready}
              title={revealed ? t("common.token.hideTitle") : t("common.token.showTitle")}
              aria-pressed={revealed}
              onClick={() => setRevealed((v) => !v)}
            >
              {revealed ? t("common.token.hide") : t("common.token.show")}
            </Button>
            <CopyButton getText={() => value} title={t("common.token.copyTokenTitle")} />
          </div>
          <p className="tok-note">{t("common.token.scopeNote", { name: bank.name })}</p>

          <label className="fs-label">{t("common.token.entryLabel")}</label>
          <Input value={entry} spellCheck={false} onChange={(e) => handleEntryChange(e.target.value)} />
          <p className="tok-note">{entryHint}</p>

          <Segmented
            className="tok-tabs"
            value={scope}
            onChange={(v) => setScope(v as "literal" | "template")}
            options={[
              { label: t("common.token.scope.literal"), value: "literal" },
              { label: t("common.token.scope.template"), value: "template" },
            ]}
          />
          <p className="tok-note">{t("common.token.scopeHint")}</p>

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

          {snippets.map((spec) => (
            <div key={spec.caption}>
              <div className="tok-caption">
                <span>{spec.caption}</span>
                <CopyButton
                  getText={() => (value != null ? spec.build(value) : null)}
                  title={t("common.token.copyToClipboard")}
                />
              </div>
              <pre className="tok-code">{spec.build(shown)}</pre>
            </div>
          ))}

          {scope === "template" && (
            <>
              <p className="tok-note">{t("common.token.generatedFileNote")}</p>
              <p className="tok-note">
                {t("common.token.manualPaste.part1")}
                <code>sed</code>
                {t("common.token.manualPaste.part2")}
                <code>{sedLineText(entry)}</code>
                {t("common.token.manualPaste.part3")}
              </p>
            </>
          )}

          {confirming && (
            <div className="tok-confirm">
              <p className="tok-confirm-text">{t("common.token.regenConfirm", { name: bank.name })}</p>
              <div className="tok-confirm-row">
                <Button size="small" onClick={() => setConfirming(false)}>
                  {t("common.btn.cancel")}
                </Button>
                <Button size="small" danger loading={regenMutation.isPending} onClick={regenerate}>
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
