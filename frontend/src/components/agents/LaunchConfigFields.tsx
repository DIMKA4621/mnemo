"use client";

import { Input, InputNumber, Segmented } from "antd";
import { useT } from "@/lib/i18n/hooks";

interface LaunchConfigFieldsProps {
  mode: "standard" | "custom";
  onModeChange: (mode: "standard" | "custom") => void;
  host: string;
  onHostChange: (host: string) => void;
  port: number | null;
  onPortChange: (port: number | null) => void;
  model: string;
  onModelChange: (model: string) => void;
}

/**
 * The launch-backend picker (`standard` Claude Code defaults vs `custom`
 * proxy host/port/model) — shared between the create-agent wizard's step 2
 * (`CreateAgentWizard`) and the ⚙ screen's Бекенд tab (`AgentBackendTab`),
 * ported from the mockup's `renderWizard`/`renderAgsBackend`
 * (`.claude/scratch/agents-page-mockup/app.js`), which duplicated this UI
 * verbatim between the two screens — this component is that duplication
 * removed. Presentational only: callers own the state, submit gating and
 * the "saved in launch.json" hint text, which differs between the two
 * (a not-yet-created agent's future path vs an existing agent's own slug).
 */
export function LaunchConfigFields({
  mode,
  onModeChange,
  host,
  onHostChange,
  port,
  onPortChange,
  model,
  onModelChange,
}: LaunchConfigFieldsProps) {
  const t = useT();
  return (
    <>
      <label className="wiz-field-label">{t("agents.wizard.backendLabel")}</label>
      <Segmented
        value={mode}
        onChange={(v) => onModeChange(v as "standard" | "custom")}
        options={[
          { label: t("agents.wizard.backendStandard"), value: "standard" },
          { label: t("agents.wizard.backendCustom"), value: "custom" },
        ]}
      />
      {mode === "standard" ? (
        <p className="wiz-hint">{t("agents.wizard.backendStandardHint")}</p>
      ) : (
        <div className="wiz-backend-fields">
          <div className="row">
            <div>
              <label className="wiz-field-label">{t("agents.wizard.hostLabel")}</label>
              <Input value={host} onChange={(e) => onHostChange(e.target.value)} />
            </div>
            <div>
              <label className="wiz-field-label">{t("agents.wizard.portLabel")}</label>
              <InputNumber
                min={1}
                max={65535}
                value={port ?? undefined}
                onChange={(v) => onPortChange(Number(v) || null)}
                style={{ width: "100%" }}
              />
            </div>
          </div>
          <div>
            <label className="wiz-field-label">{t("agents.wizard.modelLabel")}</label>
            <Input placeholder={t("agents.wizard.modelPlaceholder")} value={model} onChange={(e) => onModelChange(e.target.value)} />
          </div>
        </div>
      )}
    </>
  );
}
