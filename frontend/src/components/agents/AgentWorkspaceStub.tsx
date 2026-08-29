"use client";

import { useT } from "@/lib/i18n/hooks";
import type { AgentInfo } from "@/lib/api/agents";

interface AgentWorkspaceStubProps {
  agent: AgentInfo | null;
}

/**
 * Right pane's whole content for Фаза B — there is no chat console yet
 * (MN-43), so clicking an agent (there is nothing else to click — see
 * `AgentTreeRow`'s docstring) always lands here. Ported text from the
 * mockup's `renderWorkspace` empty-chat stub
 * (`.claude/scratch/agents-page-mockup/app.js`).
 */
export function AgentWorkspaceStub({ agent }: AgentWorkspaceStubProps) {
  const t = useT();

  if (!agent) {
    return (
      <div className="ag-ws-body">
        <p className="empty-hint" style={{ textAlign: "center" }}>
          {t("agents.workspace.selectHint")}
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="ag-ws-head">
        <div>
          <div className="ag-ws-title">{agent.name}</div>
          <div className="ag-ws-sub">{agent.slug}</div>
        </div>
      </div>
      <div className="ag-ws-body">
        <div className="ag-ws-stub">
          <div className="glyph">💬</div>
          <p>
            <strong>{t("agents.workspace.stubTitle")}</strong>
          </p>
          <p>{t("agents.workspace.stubBody")}</p>
        </div>
      </div>
    </>
  );
}
