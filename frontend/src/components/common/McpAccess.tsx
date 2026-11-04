"use client";

import { useState } from "react";
import { Button, Input, Segmented } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { copyText, type McpMode } from "@/lib/memory/tokenSnippets";
import "@/components/common/dialogs.css";

/**
 * The token row, the literal/template switch and the snippet blocks shared by
 * the bank's MCP access modal (`BankTokenPanel`) and Settings' «Admin MCP»
 * section — one look for both ways of handing a token to a project.
 */

export type { McpMode };

export function maskToken(value: string): string {
  return "•".repeat(value.length);
}

export function CopyButton({ getText, title }: { getText: () => string | null; title: string }) {
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

interface TokenRowProps {
  label: string;
  value: string | null;
  revealed: boolean;
  onRevealedChange: (next: boolean) => void;
}

export function TokenRow({ label, value, revealed, onRevealedChange }: TokenRowProps) {
  const t = useT();
  const ready = value != null;
  return (
    <>
      <label className="fs-label">{label}</label>
      <div className="tok-row">
        <Input className="tok-value" readOnly value={tokenShown(value, revealed)} disabled={!ready} />
        <Button
          disabled={!ready}
          title={revealed ? t("common.token.hideTitle") : t("common.token.showTitle")}
          aria-pressed={revealed}
          onClick={() => onRevealedChange(!revealed)}
        >
          {revealed ? t("common.token.hide") : t("common.token.show")}
        </Button>
        <CopyButton getText={() => value} title={t("common.token.copyTokenTitle")} />
      </div>
    </>
  );
}

/** What a token renders as on screen: an ellipsis while loading, dots until revealed. */
export function tokenShown(value: string | null, revealed: boolean): string {
  if (value == null) return "…";
  return revealed ? value : maskToken(value);
}

export function McpModeSwitch({ value, onChange }: { value: McpMode; onChange: (next: McpMode) => void }) {
  const t = useT();
  return (
    <>
      <Segmented
        className="tok-tabs"
        value={value}
        onChange={(v) => onChange(v as McpMode)}
        options={[
          { label: t("common.token.scope.literal"), value: "literal" },
          { label: t("common.token.scope.template"), value: "template" },
        ]}
      />
      <p className="tok-note">{t("common.token.scopeHint")}</p>
    </>
  );
}

export interface McpSnippetSpec {
  caption: string;
  /** Renders the snippet around a token — the real one for the clipboard, the masked one for the screen. */
  build: (token: string) => string;
}

interface McpSnippetsProps {
  snippets: McpSnippetSpec[];
  token: string | null;
  shown: string;
}

export function McpSnippets({ snippets, token, shown }: McpSnippetsProps) {
  const t = useT();
  return (
    <>
      {snippets.map((spec) => (
        <div key={spec.caption}>
          <div className="tok-caption">
            <span>{spec.caption}</span>
            <CopyButton
              getText={() => (token != null ? spec.build(token) : null)}
              title={t("common.token.copyToClipboard")}
            />
          </div>
          <pre className="tok-code">{spec.build(shown)}</pre>
        </div>
      ))}
    </>
  );
}

/** The template-mode footnotes: `.mcp.json` is generated, and the `sed` line a hand edit needs. */
export function TemplateNotes({ sedLine }: { sedLine: string }) {
  const t = useT();
  return (
    <>
      <p className="tok-note">{t("common.token.generatedFileNote")}</p>
      <p className="tok-note">
        {t("common.token.manualPaste.part1")}
        <code>sed</code>
        {t("common.token.manualPaste.part2")}
        <code>{sedLine}</code>
        {t("common.token.manualPaste.part3")}
      </p>
    </>
  );
}
