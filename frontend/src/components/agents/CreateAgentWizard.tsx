"use client";

import { useState } from "react";
import { Button, Input, Segmented } from "antd";
import { ModalShell } from "@/components/common/ModalShell";
import { useT } from "@/lib/i18n/hooks";
import { useCreateAgent, usePreviewAgent, usePutAgentLaunch } from "@/hooks/useAgentMutations";
import { FolderBrowser } from "./FolderBrowser";
import { LaunchConfigFields } from "./LaunchConfigFields";
import { ApiError } from "@/lib/api/fetcher";
import type { AgentInfo, AgentPreview, LaunchConfig } from "@/lib/api/agents";

const { TextArea } = Input;

const DEFAULT_CLAUDE_MD = "# CLAUDE.md\n\nDescribe the agent's role and behaviour here.\n";

interface CreateAgentWizardProps {
  open: boolean;
  onClose: () => void;
  onCreated: (agent: AgentInfo, note: string) => void;
}

/**
 * Two-step agent creation, ported from the mockup's `openWizard`/
 * `renderWizard` (`.claude/scratch/agents-page-mockup/app.js`). Step 1:
 * name + CLAUDE.md + folder source. Step 2: launch backend. MCP/Skills/Rules
 * are deliberately absent — those attach later, per-agent, on the ⚙ screen
 * (Фаза C, blocked by MN-48).
 *
 * Submission is two sequential calls (lead-specified shape, MN-42 plan):
 * `POST /api/agents` first, then `PUT /api/agents/{slug}/launch`. If the
 * second call fails the agent stays created in the backend's own default
 * (`standard`) — this shows a warning note rather than rolling anything
 * back, the same "visibly unfinished, never lost" principle
 * `agent_registry.delete()` already follows elsewhere.
 */
export function CreateAgentWizard({ open, onClose, onCreated }: CreateAgentWizardProps) {
  const t = useT();
  const createMutation = useCreateAgent();
  const putLaunchMutation = usePutAgentLaunch();
  const previewMutation = usePreviewAgent();

  const [step, setStep] = useState<1 | 2>(1);
  const [name, setName] = useState("");
  const [claudeMd, setClaudeMd] = useState(DEFAULT_CLAUDE_MD);
  const [folderMode, setFolderMode] = useState<"new" | "existing">("new");
  const [chosenFolder, setChosenFolder] = useState<string | null>(null);
  const [preview, setPreview] = useState<AgentPreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [backendMode, setBackendMode] = useState<"standard" | "custom">("standard");
  const [host, setHost] = useState("127.0.0.1");
  const [port, setPort] = useState<number | null>(8787);
  const [model, setModel] = useState("");

  const [submitError, setSubmitError] = useState<string | null>(null);

  const busy = createMutation.isPending || putLaunchMutation.isPending;

  function resetChosenFolder() {
    setChosenFolder(null);
    setPreview(null);
    setConfirmed(false);
    setPreviewError(null);
  }

  async function handlePick(path: string) {
    setPreviewError(null);
    try {
      const result = await previewMutation.mutateAsync({ root: path });
      setChosenFolder(path);
      setPreview(result);
      setConfirmed(false);
    } catch (err) {
      setPreviewError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    }
  }

  const needsConfirmation = !!preview && preview.root_exists && !preview.empty;
  const folderReady =
    folderMode === "new" ||
    (chosenFolder !== null && preview !== null && (!needsConfirmation || confirmed));

  const canAdvance = !!name.trim() && folderReady;
  const canSubmit =
    backendMode === "standard" || (host.trim() !== "" && port !== null && port >= 1 && port <= 65535);

  async function submit() {
    if (!canSubmit || busy) return;
    setSubmitError(null);
    try {
      const agent = await createMutation.mutateAsync({
        name: name.trim(),
        root: folderMode === "existing" ? chosenFolder : undefined,
        claude_md: claudeMd,
        confirm_adopt: confirmed,
      });

      const launch: LaunchConfig =
        backendMode === "custom"
          ? { mode: "custom", host: host.trim(), port: port as number, ...(model.trim() ? { model: model.trim() } : {}) }
          : { mode: "standard" };

      try {
        await putLaunchMutation.mutateAsync({ slug: agent.slug, config: launch });
        onCreated(agent, t("agents.wizard.createdNote", { name: agent.name }));
      } catch (err) {
        const message = err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err);
        onCreated(agent, t("agents.wizard.launchWarnNote", { name: agent.name, error: message }));
      }
      onClose();
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <ModalShell
      open={open}
      title={t("agents.wizard.title")}
      ariaLabel={t("agents.wizard.ariaLabel")}
      onClose={onClose}
      busy={busy}
      wide
      footer={
        step === 1 ? (
          <>
            <Button onClick={onClose}>{t("common.btn.cancel")}</Button>
            <Button type="primary" disabled={!canAdvance} onClick={() => setStep(2)}>
              {t("agents.wizard.next")}
            </Button>
          </>
        ) : (
          <>
            <Button onClick={() => setStep(1)} disabled={busy}>
              {t("common.btn.back")}
            </Button>
            <Button type="primary" disabled={!canSubmit} loading={busy} onClick={submit}>
              {busy ? t("agents.wizard.creating") : t("agents.wizard.submit")}
            </Button>
          </>
        )
      }
    >
      <div className="wiz-steps">
        <div className={`wiz-step${step === 1 ? " is-active" : " is-done"}`}>
          <span className="wiz-step-mark">1</span>
          <span>{t("agents.wizard.step1")}</span>
        </div>
        <div className="wiz-step-line" />
        <div className={`wiz-step${step === 2 ? " is-active" : ""}`}>
          <span className="wiz-step-mark">2</span>
          <span>{t("agents.wizard.step2")}</span>
        </div>
      </div>

      {step === 1 ? (
        <>
          <label className="wiz-field-label" htmlFor="wiz-name">{t("agents.wizard.nameLabel")}</label>
          <Input
            id="wiz-name"
            value={name}
            placeholder={t("agents.wizard.namePlaceholder")}
            onChange={(e) => setName(e.target.value)}
          />

          <label className="wiz-field-label" htmlFor="wiz-claude">{t("agents.wizard.claudeMdLabel")}</label>
          <TextArea id="wiz-claude" rows={7} className="reg-code" value={claudeMd} onChange={(e) => setClaudeMd(e.target.value)} />

          <label className="wiz-field-label">{t("agents.wizard.sourceLabel")}</label>
          <div className="wiz-source-row">
            <Segmented
              value={folderMode}
              onChange={(v) => {
                setFolderMode(v as "new" | "existing");
                resetChosenFolder();
              }}
              options={[
                { label: t("agents.wizard.sourceNew"), value: "new" },
                { label: t("agents.wizard.sourceExisting"), value: "existing" },
              ]}
            />
          </div>

          {folderMode === "new" ? (
            <p className="wiz-hint">{t("agents.wizard.newFolderHint")}</p>
          ) : (
            <>
              <FolderBrowser onPick={handlePick} busy={previewMutation.isPending} />
              {previewError && <p className="modal-error">{t("agents.wizard.previewError", { message: previewError })}</p>}
              {needsConfirmation && !confirmed && preview && (
                <div className="wiz-confirm">
                  <p className="wiz-confirm-title">{t("agents.wizard.confirm.title")}</p>
                  <ul className="wiz-confirm-list">
                    <li>
                      {t("agents.wizard.confirm.claudeMd")}:{" "}
                      <span className="v">{preview.has_claude_md ? t("agents.wizard.confirm.yes") : t("agents.wizard.confirm.no")}</span>
                    </li>
                    <li>
                      {t("agents.wizard.confirm.mcp")}: <span className="v">{preview.mcp_server_names.length}</span>
                    </li>
                    <li>
                      {t("agents.wizard.confirm.skills")}: <span className="v">{preview.skill_dirs.length}</span>
                    </li>
                    <li>
                      {t("agents.wizard.confirm.rules")}: <span className="v">{preview.rule_files.length}</span>
                    </li>
                  </ul>
                  <p className="wiz-hint">{t("agents.wizard.confirm.note")}</p>
                  <div className="wiz-confirm-row">
                    <Button onClick={resetChosenFolder}>{t("agents.wizard.confirm.cancel")}</Button>
                    <Button type="primary" onClick={() => setConfirmed(true)}>
                      {t("agents.wizard.confirm.ok")}
                    </Button>
                  </div>
                </div>
              )}
              {chosenFolder && (!needsConfirmation || confirmed) && (
                <p className="wiz-hint" style={{ color: "var(--ok)" }}>
                  {t("agents.wizard.chosenNote", { path: chosenFolder })}
                </p>
              )}
            </>
          )}
        </>
      ) : (
        <>
          <LaunchConfigFields
            mode={backendMode}
            onModeChange={setBackendMode}
            host={host}
            onHostChange={setHost}
            port={port}
            onPortChange={setPort}
            model={model}
            onModelChange={setModel}
          />
          <p className="wiz-hint" style={{ marginTop: 10 }}>{t("agents.wizard.backendSavedHint")}</p>
          <p className="wiz-hint">{t("agents.wizard.linksHint")}</p>
          {submitError && <p className="modal-error">{submitError}</p>}
        </>
      )}
    </ModalShell>
  );
}
