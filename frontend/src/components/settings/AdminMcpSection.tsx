"use client";

import { useState } from "react";
import { Alert, Button, Skeleton } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useAdminMcp } from "@/hooks/useSettingsQueries";
import {
  McpModeSwitch,
  McpSnippets,
  TemplateNotes,
  TokenRow,
  tokenShown,
  type McpSnippetSpec,
} from "@/components/common/McpAccess";
import type { AdminMcpInfo } from "@/lib/api/adminMcp";
import type { McpMode } from "@/lib/memory/tokenSnippets";
import { AdminMcpWiringDialog } from "./AdminMcpWiringDialog";

const ADMIN_TOOLS = ["banks", "bank_add", "bank_remove", "bank_state", "reindex", "status", "logs"];

function adminSnippets(info: AdminMcpInfo, mode: McpMode, t: ReturnType<typeof useT>): McpSnippetSpec[] {
  // The backend renders the snippets with the real token; masking swaps it back out.
  const around = (text: string) => (tok: string) => text.split(info.token).join(tok);
  if (mode === "literal") {
    return [{ caption: t("common.token.caption.literal"), build: around(JSON.stringify(info.snippet.literal, null, 2)) }];
  }
  return [
    { caption: t("common.token.caption.template"), build: around(JSON.stringify(info.snippet.template.template_entry, null, 2)) },
    { caption: t("common.token.caption.env"), build: around(info.snippet.template.env_block) },
  ];
}

export function AdminMcpSection() {
  const t = useT();
  const query = useAdminMcp();
  const [revealed, setRevealed] = useState(false);
  const [mode, setMode] = useState<McpMode>("literal");
  const [wiringOpen, setWiringOpen] = useState(false);

  const info = query.data ?? null;
  const token = info?.token ?? null;

  return (
    <>
      <p className="set-note">
        {t("settings.admin.intro")}{" "}
        {ADMIN_TOOLS.map((tool, i) => (
          <span key={tool}>
            {i > 0 && ", "}
            <code>{tool}</code>
          </span>
        ))}
        .
      </p>
      <Alert type="warning" showIcon title={t("settings.admin.warning")} style={{ marginBottom: 12 }} />

      {query.error ? (
        <Alert
          type="error"
          showIcon
          title={query.error instanceof Error ? query.error.message : String(query.error)}
          action={
            <Button size="small" onClick={() => query.refetch()}>
              {t("settings.admin.retry")}
            </Button>
          }
        />
      ) : !info ? (
        <Skeleton active title={false} paragraph={{ rows: 6 }} />
      ) : (
        <>
          <TokenRow label={t("settings.admin.tokenLabel")} value={token} revealed={revealed} onRevealedChange={setRevealed} />
          <p className="tok-note">{t("settings.admin.tokenNote", { entry: info.entry_name })}</p>

          <McpModeSwitch value={mode} onChange={setMode} />

          {mode === "template" && <p className="tok-lead">{t("settings.admin.templateLead")}</p>}

          <McpSnippets snippets={adminSnippets(info, mode, t)} token={token} shown={tokenShown(token, revealed)} />

          {mode === "template" && <TemplateNotes sedLine={info.snippet.template.sed_line} />}

          <div style={{ marginTop: 14 }}>
            <Button type="primary" onClick={() => setWiringOpen(true)}>
              {t("settings.admin.wireBtn")}
            </Button>
            <p className="tok-note">{t("settings.admin.wireHint")}</p>
          </div>
        </>
      )}

      <AdminMcpWiringDialog open={wiringOpen} mode={mode} onModeChange={setMode} onClose={() => setWiringOpen(false)} />
    </>
  );
}
