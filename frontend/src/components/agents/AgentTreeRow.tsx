"use client";

import { useT } from "@/lib/i18n/hooks";
import { useAgentChats } from "@/hooks/useAgentQueries";
import { useCreateChat, useDeleteChat } from "@/hooks/useAgentMutations";
import type { AgentInfo } from "@/lib/api/agents";
import type { ChatInfo } from "@/lib/api/agentChats";

interface AgentTreeRowProps {
  agent: AgentInfo;
  expanded: boolean;
  selected: boolean;
  selectedChatId: string | null;
  onToggle: () => void;
  onOpenSettings: () => void;
  onSelectChat: (chatId: string) => void;
  onChatDeleted: (chatId: string) => void;
}

/**
 * One agent row plus its real chats strip (MN-44 — MN-42 shipped this
 * always-empty, since MN-43's chat backend didn't exist yet; see this file's
 * own git history for that earlier state). Ported layout from the mockup's
 * `renderTree` (`.claude/scratch/agents-page-mockup/app.js`), now backed by
 * `useAgentChats(agent.slug)` instead of a hardcoded empty list.
 *
 * Kept mounted (not gated behind `expanded`) so the agent's chat COUNT is a
 * real fact even while collapsed — same reasoning `AgentsHeader` already
 * documents for its own total. The chat rows themselves still only render
 * while `expanded`.
 *
 * The ⚙ gear button opens the agent-settings screen (Фаза C). It sits
 * inside the row's own click target, so it stops propagation before firing
 * `onOpenSettings` — otherwise clicking it would also toggle the chat strip,
 * same nesting the mockup's own CSS comment calls out (`.ag-agent-gear`:
 * "a real, separately clickable button").
 */
export function AgentTreeRow({
  agent,
  expanded,
  selected,
  selectedChatId,
  onToggle,
  onOpenSettings,
  onSelectChat,
  onChatDeleted,
}: AgentTreeRowProps) {
  const t = useT();
  const chatsQuery = useAgentChats(agent.slug);
  const createChat = useCreateChat();
  const deleteChat = useDeleteChat();
  const chats = chatsQuery.data ?? [];

  function chatLabel(chat: ChatInfo): string {
    return chat.title || t("agents.tree.chatUntitled");
  }

  async function handleNewChat(ev: React.MouseEvent) {
    ev.stopPropagation();
    const chat = await createChat.mutateAsync({ slug: agent.slug });
    onSelectChat(chat.chat_id);
  }

  function handleDeleteChat(ev: React.MouseEvent, chatId: string) {
    ev.stopPropagation();
    deleteChat.mutate(
      { slug: agent.slug, chatId },
      { onSuccess: () => onChatDeleted(chatId) },
    );
  }

  return (
    <div className="ag-agent">
      <div
        className={`ag-agent-row${selected ? " is-selected" : ""}`}
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(ev) => {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            onToggle();
          }
        }}
      >
        <span className="ag-agent-title">
          <span className="ag-agent-name">{agent.name}</span>
        </span>
        <span className="ag-agent-count">{chats.length}</span>
        <button
          type="button"
          className="ag-agent-gear"
          title={t("agents.tree.settingsTitle")}
          aria-label={t("agents.tree.settingsTitle")}
          onClick={(ev) => {
            ev.stopPropagation();
            onOpenSettings();
          }}
        >
          ⚙
        </button>
        <span className="ag-twisty" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <div className="ag-chats">
          {chatsQuery.isLoading && <div className="ag-no-chats">{t("agents.tree.loading")}</div>}
          {!chatsQuery.isLoading && chats.length === 0 && (
            <div className="ag-no-chats">{t("agents.tree.noChats")}</div>
          )}
          {chats.map((chat) => (
            <div
              key={chat.chat_id}
              className={`ag-chat-row${chat.chat_id === selectedChatId ? " is-selected" : ""}`}
              role="button"
              tabIndex={0}
              onClick={() => onSelectChat(chat.chat_id)}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === " ") {
                  ev.preventDefault();
                  onSelectChat(chat.chat_id);
                }
              }}
            >
              <span className="ag-chat-title">{chatLabel(chat)}</span>
              <button
                type="button"
                className="ag-chat-delete"
                title={t("agents.tree.deleteChatTitle")}
                aria-label={t("agents.tree.deleteChatTitle")}
                disabled={deleteChat.isPending}
                onClick={(ev) => handleDeleteChat(ev, chat.chat_id)}
              >
                🗑
              </button>
            </div>
          ))}
          <button
            type="button"
            className="ag-new-chat"
            disabled={createChat.isPending}
            onClick={(ev) => void handleNewChat(ev)}
          >
            {createChat.isPending ? t("agents.tree.creatingChat") : t("agents.tree.newChat")}
          </button>
        </div>
      )}
    </div>
  );
}
