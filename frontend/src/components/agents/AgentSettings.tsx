"use client";

import { useState } from "react";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { AgentGeneralTab } from "./AgentGeneralTab";
import { AgentBackendTab } from "./AgentBackendTab";
import { AgentLinksTab } from "./AgentLinksTab";
import type { AgentInfo } from "@/lib/api/agents";

/** What a Save-gated tab (Загальне, Бекенд) hands the shared footer button —
 *  mirrors `SettingsTabs.tsx`'s `SectionController` (same idea: each tab
 *  registers its own `{hasPendingChange, busy, submit}` while mounted and
 *  clears it on unmount, switching tabs unmounts the previous one so its
 *  controller and any pending edit drop together). Defined locally rather
 *  than imported from the Settings feature — same shape, no cross-feature
 *  coupling. MCP/Skills/Rules registers none: every action there (attach,
 *  edit, detach) is already an immediate mutation, so there is nothing for
 *  a shared Save button to do and the footer is hidden entirely for it,
 *  same as Налаштування's Обслуговування tab. */
export interface AgentSectionController {
  hasPendingChange: boolean;
  busy: boolean;
  submit: () => void | Promise<void>;
}

type TabId = "general" | "backend" | "links";

interface AgentSettingsProps {
  agent: AgentInfo;
  onClose: () => void;
}

/**
 * The ⚙ screen — fills the same workspace pane a chat would, not a route:
 * it belongs to one agent, not to the app (`.claude/scratch/agents-page-
 * mockup/app.js`'s `renderAgentSettings` docstring makes this call
 * explicitly, and it is followed here rather than re-decided). Tabs:
 * Загальне (name + CLAUDE.md), Бекенд (launch config), and "MCP / Skills /
 * Rules" — English on purpose, the lead's unify decision on MN-42's Jira
 * ticket (29.08.2026): this is Claude Code's own vocabulary (like
 * CLAUDE.md), not an arbitrary mnemo feature, so translating "Rules" while
 * leaving "MCP"/"Skills" in English was already an inconsistency in the
 * mockup this fixes.
 */
export function AgentSettings({ agent, onClose }: AgentSettingsProps) {
  const t = useT();
  const [active, setActive] = useState<TabId>("general");
  const [controller, setController] = useState<AgentSectionController | null>(null);

  return (
    <>
      <div className="ag-ws-head">
        <div>
          <div className="ag-ws-title">{t("agents.settings.title")}</div>
          <div className="ag-ws-sub">{agent.name}</div>
        </div>
        <div style={{ flex: 1 }} />
        <button type="button" className="icon-btn" title={t("common.btn.close")} aria-label={t("common.btn.close")} onClick={onClose}>
          ✕
        </button>
      </div>

      <div className="ags-tabs">
        <button type="button" className={`ags-tab${active === "general" ? " is-active" : ""}`} onClick={() => setActive("general")}>
          {t("agents.settings.tabs.general")}
        </button>
        <button type="button" className={`ags-tab${active === "backend" ? " is-active" : ""}`} onClick={() => setActive("backend")}>
          {t("agents.settings.tabs.backend")}
        </button>
        <button type="button" className={`ags-tab${active === "links" ? " is-active" : ""}`} onClick={() => setActive("links")}>
          {t("agents.settings.tabs.links")}
        </button>
      </div>

      <div className="ags-body">
        {active === "general" && <AgentGeneralTab agent={agent} onController={setController} />}
        {active === "backend" && <AgentBackendTab agent={agent} onController={setController} />}
        {active === "links" && <AgentLinksTab agent={agent} />}
      </div>

      {controller && (
        <div className="ags-foot">
          <Button
            type="primary"
            disabled={!controller.hasPendingChange || controller.busy}
            loading={controller.busy}
            onClick={() => controller.submit()}
          >
            {t("agents.settings.save")}
          </Button>
        </div>
      )}
    </>
  );
}
