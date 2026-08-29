"use client";

import { useEffect, useState } from "react";
import { AgentsHeader } from "./AgentsHeader";
import { AgentTree } from "./AgentTree";
import { AgentWorkspaceStub } from "./AgentWorkspaceStub";
import { AgentSettings } from "./AgentSettings";
import { ChatConsole } from "./ChatConsole";
import { CreateAgentWizard } from "./CreateAgentWizard";
import { PaneResizer } from "@/components/common/PaneResizer";
import { useInlineNote, InlineNote } from "@/components/common/InlineNote";
import { useAgentsPaneWidthStore } from "@/lib/store/agents-pane-width";
import { useAgents } from "@/hooks/useAgentQueries";
import type { AgentInfo } from "@/lib/api/agents";
import type { ChatInfo } from "@/lib/api/agentChats";
import "./agents.css";
import "@/components/common/dialogs.css";

/**
 * Агенти: дерево агент→чати (tree) + workspace, ported layout from the
 * live-verified mockup's `layout-agents`
 * (`.claude/scratch/agents-page-mockup/index.html`). No sub-nav/"Чати" tab —
 * this is the page's one and only content, same as every other route.
 */
export default function AgentsPage() {
  const width = useAgentsPaneWidthStore((s) => s.width);
  const hydrateWidth = useAgentsPaneWidthStore((s) => s.hydrate);
  const beginDrag = useAgentsPaneWidthStore((s) => s.beginDrag);
  const applyDrag = useAgentsPaneWidthStore((s) => s.applyDrag);
  const commitDrag = useAgentsPaneWidthStore((s) => s.commitDrag);

  const agentsQuery = useAgents();

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null);
  // Fills the same workspace pane a chat would, not a route — the ⚙ screen
  // belongs to one agent, not to the app (mockup's `renderAgentSettings`
  // docstring, `.claude/scratch/agents-page-mockup/app.js`, makes this call
  // explicitly; followed here rather than re-decided, MN-42 Фаза C).
  const [workspaceMode, setWorkspaceMode] = useState<"chat" | "settings">("chat");
  const [wizardOpen, setWizardOpen] = useState(false);
  const [note, setNote] = useInlineNote();

  useEffect(() => {
    hydrateWidth();
  }, [hydrateWidth]);

  function toggleAgent(slug: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
    setSelectedSlug(slug);
    setWorkspaceMode("chat");
    // Clicking the agent row itself (as opposed to one of its chats) always
    // lands on the "pick or start a chat" hint — the tree, not this pane,
    // is what actually opens a chat (`selectChat` below).
    setSelectedChatId(null);
  }

  function selectChat(agentSlug: string, chatId: string) {
    setExpanded((prev) => new Set(prev).add(agentSlug));
    setSelectedSlug(agentSlug);
    setWorkspaceMode("chat");
    setSelectedChatId(chatId);
  }

  // Deleting the chat currently open in the workspace pane must fall back
  // to the "pick or start a chat" hint — otherwise `ChatConsole` would keep
  // rendering against a `chat_id` the backend just 404s on reconnect.
  function handleChatDeleted(chatId: string) {
    setSelectedChatId((cur) => (cur === chatId ? null : cur));
  }

  function openSettings(slug: string) {
    setSelectedSlug(slug);
    setWorkspaceMode("settings");
  }

  function handleCreated(agent: AgentInfo, message: string) {
    setExpanded((prev) => new Set(prev).add(agent.slug));
    setSelectedSlug(agent.slug);
    setWorkspaceMode("chat");
    setSelectedChatId(null);
    setNote(message);
  }

  // MN-45 Phase C: unlike `handleCreated` above, a launch already has a real
  // chat (the backend creates agent + chat atomically) — open straight into
  // it via `selectChat` rather than landing on the "pick or start a chat"
  // stub.
  function handleSubagentLaunched(agent: AgentInfo, chat: ChatInfo, message: string) {
    selectChat(agent.slug, chat.chat_id);
    setNote(message);
  }

  const selectedAgent: AgentInfo | null = agentsQuery.data?.find((a) => a.slug === selectedSlug) ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <AgentsHeader onAdd={() => setWizardOpen(true)} />
      <div className="ag-layout" style={{ gridTemplateColumns: `${width}px 6px minmax(0, 1fr)` }}>
        <div className="ag-tree-pane">
          <AgentTree
            expanded={expanded}
            selectedSlug={selectedSlug}
            selectedChatId={selectedChatId}
            onToggle={toggleAgent}
            onOpenSettings={openSettings}
            onSelectChat={selectChat}
            onChatDeleted={handleChatDeleted}
          />
        </div>
        <PaneResizer onStart={beginDrag} onDrag={applyDrag} onCommit={commitDrag} />
        <div className="ag-workspace">
          {workspaceMode === "settings" && selectedAgent ? (
            <AgentSettings
              key={selectedAgent.slug}
              agent={selectedAgent}
              onClose={() => setWorkspaceMode("chat")}
              onLaunched={handleSubagentLaunched}
            />
          ) : workspaceMode === "chat" && selectedAgent && selectedChatId ? (
            <ChatConsole key={selectedChatId} agent={selectedAgent} chatId={selectedChatId} />
          ) : (
            <AgentWorkspaceStub agent={selectedAgent} />
          )}
        </div>
      </div>
      <CreateAgentWizard
        key={wizardOpen ? "wizard-open" : "wizard-closed"}
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={handleCreated}
      />
      <InlineNote text={note} tone="success" />
    </div>
  );
}
