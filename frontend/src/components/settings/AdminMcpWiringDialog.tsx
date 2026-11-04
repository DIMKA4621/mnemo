"use client";

import { useState } from "react";
import { Alert, Button, Flex, Skeleton } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { ModalShell } from "@/components/common/ModalShell";
import { FolderBrowser } from "@/components/agents/FolderBrowser";
import { FileTags, NoteAlerts, UntrackHint } from "@/components/common/WiringPreviewParts";
import { useAdminWiringPreview } from "@/hooks/useSettingsQueries";
import { useWireAdminMcp } from "@/hooks/useSettingsMutations";
import { ApiError } from "@/lib/api/fetcher";
import type { AdminWiringPreview, AdminWiringResult } from "@/lib/api/adminMcp";
import type { McpMode } from "@/lib/memory/tokenSnippets";

interface AdminMcpWiringDialogProps {
  open: boolean;
  /** The section's own literal/template switch — the dialog previews and writes that form. */
  mode: McpMode;
  onModeChange: (next: McpMode) => void;
  onClose: () => void;
}

export function AdminMcpWiringDialog({ open, mode, onModeChange, onClose }: AdminMcpWiringDialogProps) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  return (
    <ModalShell open={open} title={t("settings.admin.dialog.title")} onClose={onClose} busy={busy} wide>
      {/* ModalShell destroys hidden content, so every open starts at the folder browser. */}
      <WiringFlow mode={mode} onModeChange={onModeChange} onClose={onClose} onBusyChange={setBusy} />
    </ModalShell>
  );
}

interface WiringFlowProps {
  mode: McpMode;
  onModeChange: (next: McpMode) => void;
  onClose: () => void;
  onBusyChange: (busy: boolean) => void;
}

function WiringFlow({ mode, onModeChange, onClose, onBusyChange }: WiringFlowProps) {
  const t = useT();
  const [root, setRoot] = useState<string | null>(null);
  const [result, setResult] = useState<AdminWiringResult | null>(null);
  const previewQuery = useAdminWiringPreview(result ? null : root, mode);
  const wire = useWireAdminMcp();

  function back() {
    wire.reset();
    setResult(null);
    setRoot(null);
  }

  // A refusal from the last write describes the old mode; drop it with the switch.
  function switchMode(next: McpMode) {
    wire.reset();
    onModeChange(next);
  }

  async function confirm() {
    if (!root) return;
    onBusyChange(true);
    try {
      setResult(await wire.mutateAsync({ root, mode }));
    } catch {
      // Rendered from `wire.error` below.
    } finally {
      onBusyChange(false);
    }
  }

  if (result) {
    return <WiringDone result={result} onClose={onClose} onAnother={back} />;
  }

  if (!root) {
    return (
      <>
        <p className="tok-note" style={{ marginTop: 0 }}>{t("settings.admin.dialog.pickHint")}</p>
        <FolderBrowser onPick={setRoot} />
      </>
    );
  }

  const wireError = wire.error instanceof ApiError ? wire.error : null;
  const refusedPreview = (wireError?.detail?.preview as AdminWiringPreview | undefined) ?? null;
  const preview = refusedPreview ?? previewQuery.data ?? null;
  const canWrite = !refusedPreview && preview != null && (preview.action === "create" || preview.action === "add");

  return (
    <>
      <p className="tok-note" style={{ marginTop: 0 }}>
        {t("settings.admin.dialog.folder")} <code>{root}</code>
      </p>

      {previewQuery.isFetching && !preview ? (
        <Skeleton active title={false} paragraph={{ rows: 4 }} />
      ) : previewQuery.error ? (
        <Alert type="error" showIcon title={errorText(previewQuery.error)} />
      ) : preview ? (
        <PreviewView preview={preview} onModeChange={switchMode} switchDisabled={wire.isPending} />
      ) : null}

      {wire.error != null && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 10 }}
          title={t("settings.admin.dialog.writeFailed")}
          description={errorText(wire.error)}
        />
      )}

      <Flex gap={8} justify="flex-end" style={{ marginTop: 14 }}>
        <Button onClick={back} disabled={wire.isPending}>
          {t("settings.admin.dialog.otherFolder")}
        </Button>
        {canWrite && (
          <Button type="primary" loading={wire.isPending} onClick={confirm}>
            {t("settings.admin.dialog.confirm", { target: preview.target })}
          </Button>
        )}
        {!canWrite && <Button onClick={onClose}>{t("common.btn.close")}</Button>}
      </Flex>
    </>
  );
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Whether the other form would change the outcome — a missing folder or broken JSON stays refused either way. */
function modeMatters(preview: AdminWiringPreview): boolean {
  return preview.action === "create" || preview.action === "add" || preview.reason === "tracked" || preview.reason === "needs_migrate" || preview.reason === "env_conflict";
}

interface PreviewViewProps {
  preview: AdminWiringPreview;
  onModeChange: (next: McpMode) => void;
  switchDisabled: boolean;
}

function PreviewView({ preview, onModeChange, switchDisabled }: PreviewViewProps) {
  const t = useT();
  const other: McpMode = preview.mode === "literal" ? "template" : "literal";
  const modeLabel = t(`settings.admin.dialog.mode.${preview.mode}`);
  const switchBtn = (
    <Button size="small" disabled={switchDisabled} onClick={() => onModeChange(preview.suggested_mode)}>
      {t(`settings.admin.dialog.switchTo.${preview.suggested_mode}`)}
    </Button>
  );

  return (
    <Flex vertical gap={10}>
      <ActionSummary preview={preview} modeLabel={modeLabel} />

      {preview.warnings.includes("template_layer") && (
        <Alert type="warning" showIcon title={t("settings.admin.dialog.warn.templateLayer")} action={switchBtn} />
      )}
      {preview.suggested_mode !== preview.mode && !preview.warnings.includes("template_layer") && modeMatters(preview) && (
        <Alert
          type="info"
          showIcon
          title={t(`settings.admin.dialog.suggest.${other}`)}
          action={switchBtn}
        />
      )}

      <NoteAlerts notes={preview.notes} />
    </Flex>
  );
}

function ActionSummary({ preview, modeLabel }: { preview: AdminWiringPreview; modeLabel: string }) {
  const t = useT();
  const target = preview.target;

  if (preview.action === "refused") {
    const reason = preview.reason ?? "not_found";
    return (
      <Alert
        type="error"
        showIcon
        title={t("settings.admin.dialog.refused.title")}
        description={
          <>
            {t(`settings.admin.dialog.refused.${reason}`, { target })}
            {reason === "tracked" && (
              <UntrackHint files={preview.tracked_files} />
            )}
          </>
        }
      />
    );
  }

  if (preview.action === "already") {
    return (
      <Alert
        type={preview.entry_current ? "success" : "warning"}
        showIcon
        title={t("settings.admin.dialog.already.title", { target })}
        description={t(preview.entry_current ? "settings.admin.dialog.already.current" : "settings.admin.dialog.already.different")}
      />
    );
  }

  return (
    <div>
      <p className="tok-lead" style={{ marginTop: 0 }}>
        {t(`settings.admin.dialog.action.${preview.action}`, { target, mode: modeLabel })}
      </p>
      <p className="tok-note">{t("settings.admin.dialog.writes")}</p>
      <FileTags files={preview.writes} />
      {preview.mode === "template" && <p className="tok-note">{t("settings.admin.dialog.templateRun")}</p>}
    </div>
  );
}

function WiringDone({ result, onClose, onAnother }: { result: AdminWiringResult; onClose: () => void; onAnother: () => void }) {
  const t = useT();
  const regen = result.regenerated;
  return (
    <Flex vertical gap={10}>
      <Alert
        type="success"
        showIcon
        title={result.written.length ? t("settings.admin.done.title") : t("settings.admin.done.nothing")}
        description={
          <>
            <code>{result.preview.project_root}</code>
            {result.written.length > 0 && <FileTags files={result.written} />}
          </>
        }
      />
      {regen && (
        <Alert
          type={regen.ok ? "info" : "warning"}
          showIcon
          title={t(regen.ok ? "settings.admin.done.regenOk" : "settings.admin.done.regenFailed", { script: regen.script })}
          description={regen.output.length ? <pre className="tok-code">{regen.output.join("\n")}</pre> : undefined}
        />
      )}
      <NoteAlerts notes={result.notes} />
      <p className="tok-note">{t("settings.admin.done.restartHint")}</p>
      <Flex gap={8} justify="flex-end">
        <Button onClick={onAnother}>{t("settings.admin.dialog.otherFolder")}</Button>
        <Button type="primary" onClick={onClose}>
          {t("common.btn.close")}
        </Button>
      </Flex>
    </Flex>
  );
}
