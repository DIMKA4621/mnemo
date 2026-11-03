"use client";

import { Alert, Collapse, Flex, Tag, Typography } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { NoteAlerts, UntrackHint } from "@/components/common/WiringPreviewParts";
import type { InitFileAction, InitPreview, InitPreviewReason } from "@/lib/api/memory";

const GROUPS: InitFileAction[] = ["create", "update", "unchanged"];
const GROUP_COLOR: Record<InitFileAction, string | undefined> = { create: "green", update: "blue", unchanged: undefined };

/** Refusals where the bank itself cannot be registered either, so "add without MCP" is no way out. */
const BLOCKS_BANK: InitPreviewReason[] = ["bank_exists", "root_not_found"];

/** Whether the picker may still create the bank with `init: false` after this preview. */
export function canAddWithoutMcp(preview: InitPreview): boolean {
  if (preview.action === "skipped") return true;
  return preview.action === "refused" && !BLOCKS_BANK.includes(preview.reason ?? "root_not_found");
}

/** An unparsable `.mcp.json` that `overwrite_invalid` would back up and rebuild. */
export function canOverwriteInvalid(preview: InitPreview): boolean {
  return preview.action === "refused" && preview.reason === "invalid_json" && !!preview.overridable;
}

export function InitPreviewView({ preview }: { preview: InitPreview }) {
  const t = useT();
  const log = preview.log ?? [];
  const backup = preview.overwrite_invalid ? preview.files.find((f) => f.reason === "backup") : undefined;

  if (preview.action === "skipped") {
    return (
      <Alert
        type="warning"
        showIcon
        title={t("common.picker.preview.skipped.title")}
        description={t("common.picker.preview.skipped.body", { root: preview.bank_root })}
      />
    );
  }

  if (preview.action === "refused") {
    const reason = preview.reason ?? "root_not_found";
    const jsonError = canOverwriteInvalid(preview) ? preview.json_error : null;
    let offer = canAddWithoutMcp(preview) ? "common.picker.preview.refused.offer" : "common.picker.preview.refused.noOffer";
    if (canOverwriteInvalid(preview)) offer = "common.picker.preview.overwrite.offer";
    return (
      <Alert
        type="error"
        showIcon
        title={t("common.picker.preview.refused.title")}
        description={
          <>
            {jsonError
              ? t("common.picker.preview.overwrite.refused")
              : t(`common.picker.preview.refused.${reason}`, { project: preview.project_root ?? "" })}
            {reason === "tracked" && <UntrackHint files={preview.tracked_files ?? []} />}
            {jsonError ? (
              <pre className="tok-code">
                {t("common.picker.preview.overwrite.location", {
                  line: jsonError.line,
                  column: jsonError.column,
                  message: jsonError.message,
                })}
              </pre>
            ) : (
              preview.detail && <pre className="tok-code">{preview.detail}</pre>
            )}
            <p className="tok-note">{t(offer)}</p>
          </>
        }
      />
    );
  }

  const script = preview.setup_script;
  return (
    <Flex vertical gap={10}>
      <p className="tok-lead" style={{ marginTop: 0 }}>
        {backup ? t("common.picker.preview.overwrite.lead") : t(`common.picker.preview.action.${preview.action}`)}{" "}
        <code>{preview.project_root}</code>
      </p>

      {backup && (
        <Alert
          type="warning"
          showIcon
          title={t("common.picker.preview.overwrite.planTitle")}
          description={t("common.picker.preview.overwrite.planBody", { backup: backup.path })}
        />
      )}

      {preview.warnings.map((w) => (
        <Alert
          key={w}
          type="warning"
          showIcon
          title={t(`common.picker.preview.warn.${w}`, {
            servers: (preview.carried_servers ?? []).join(", "),
            missing: (script?.missing ?? []).join(", "),
            script: script?.name ?? "mcp-setup",
          })}
        />
      ))}

      {GROUPS.map((group) => {
        const files = preview.files.filter((f) => f.action === group);
        if (!files.length) return null;
        return (
          <div key={group}>
            <p className="tok-note" style={{ marginTop: 0 }}>{t(`common.picker.preview.group.${group}`, { n: files.length })}</p>
            <Flex vertical gap={2} style={{ marginTop: 4 }}>
              {files.map((f) => (
                <Flex key={f.path} gap={8} align="baseline" wrap>
                  <Tag color={GROUP_COLOR[group]}>{f.path}</Tag>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t(`common.picker.preview.reason.${f.reason}`)}
                  </Typography.Text>
                </Flex>
              ))}
            </Flex>
          </div>
        );
      })}

      {script && (
        <p className="tok-note" style={{ marginTop: 0 }}>
          {script.runs
            ? script.missing.length
              ? t("common.picker.preview.script.blocked", { script: script.name })
              : t("common.picker.preview.script.runs", { script: script.name })
            : t("common.picker.preview.script.none")}
        </p>
      )}

      <NoteAlerts notes={preview.notes} />

      {log.length > 0 && (
        <Collapse
          size="small"
          ghost
          items={[
            {
              key: "log",
              label: t("common.picker.preview.log"),
              children: <pre className="tok-code">{log.join("\n")}</pre>,
            },
          ]}
        />
      )}
    </Flex>
  );
}
