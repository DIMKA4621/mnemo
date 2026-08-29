import { api, apiUpload } from "./fetcher";

/** MN-43/MN-44. Lifecycle only — `agent_registry.py`'s `{chat_id, title,
 *  created_at, last_active_at}` shape. The live PTY session behind a chat
 *  is never touched through this module: it lives on `/ws/agents/{slug}/
 *  chats/{chat_id}`, owned entirely by `ChatConsole.tsx`. */
export interface ChatInfo {
  chat_id: string;
  title: string | null;
  created_at: string;
  last_active_at: string;
}

export function getChats(slug: string): Promise<{ chats: ChatInfo[] }> {
  return api(`/api/agents/${encodeURIComponent(slug)}/chats`);
}

export function createChat(slug: string, title?: string | null): Promise<ChatInfo> {
  return api(`/api/agents/${encodeURIComponent(slug)}/chats`, {
    method: "POST",
    body: { title: title ?? null },
  });
}

export function getChat(slug: string, chatId: string): Promise<ChatInfo> {
  return api(`/api/agents/${encodeURIComponent(slug)}/chats/${encodeURIComponent(chatId)}`);
}

export function deleteChat(slug: string, chatId: string): Promise<{ ok: boolean }> {
  return api(`/api/agents/${encodeURIComponent(slug)}/chats/${encodeURIComponent(chatId)}`, {
    method: "DELETE",
  });
}

/** `api_agent_chat_upload`'s response (`src/api.py`) — `path` is the
 *  absolute, server-side path `ChatConsole.tsx` inserts as literal text
 *  into the PTY input, exactly like dragging a file into a real terminal. */
export interface ChatUploadResult {
  path: string;
  filename: string;
}

export function uploadChatFile(slug: string, chatId: string, file: File): Promise<ChatUploadResult> {
  return apiUpload(`/api/agents/${encodeURIComponent(slug)}/chats/${encodeURIComponent(chatId)}/upload`, file);
}

/** `api_agent_subagents` — read-only, best-effort listing of
 *  `.claude/agents/*.md` inside this agent's own folder. No launch action
 *  (MN-45, out of scope here). */
export interface SubagentInfo {
  name: string;
  description: string | null;
}

export function getSubagents(slug: string): Promise<{ subagents: SubagentInfo[] }> {
  return api(`/api/agents/${encodeURIComponent(slug)}/subagents`);
}
