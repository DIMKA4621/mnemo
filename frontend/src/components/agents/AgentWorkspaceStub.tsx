"use client";

import { useT } from "@/lib/i18n/hooks";
import type { AgentInfo } from "@/lib/api/agents";

interface AgentWorkspaceStubProps {
  agent: AgentInfo | null;
}

/**
 * Right pane's content when no chat is open (MN-44): either no agent is
 * selected at all (`selectHint`), or an agent is selected but its own row
 * — not one of its chats — was clicked, so there's nothing to render but a
 * "pick or start a chat" hint. `AgentsPage.tsx` renders `ChatConsole`
 * instead of this the moment a `chat_id` is actually selected.
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
