import { api, apiUpload } from "./fetcher";
import type { AgentInfo } from "./agents";

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
 *  `.claude/agents/*.md` inside this agent's own folder. */
export interface SubagentInfo {
  name: string;
  description: string | null;
}

export function getSubagents(slug: string): Promise<{ subagents: SubagentInfo[] }> {
  return api(`/api/agents/${encodeURIComponent(slug)}/subagents`);
}

/** MN-45 Phase C. Promotes one subagent definition (by the same `name` a
 *  `SubagentInfo` row shows) into a brand-new, top-level agent + an empty
 *  chat ready to open — mirrors `createAgent()`/`createChat()`'s own shapes
 *  rather than inventing a new one, since that's exactly what the backend
 *  does under the hood (`agent_registry.create` + `agent_registry.create_chat`). */
export function launchSubagent(slug: string, name: string): Promise<{ agent: AgentInfo; chat: ChatInfo }> {
  return api(`/api/agents/${encodeURIComponent(slug)}/subagents/${encodeURIComponent(name)}/launch`, {
    method: "POST",
  });
}

/** One `SubagentStart`/`SubagentStop` hook event, as persisted by
 *  `agent_registry.subagents_sidecar_path` and broadcast by
 *  `agent_runtime.record_subagent_event` (MN-45b) — distinct from
 *  `SubagentInfo` above, which lists `.claude/agents/*.md` DEFINITIONS, not
 *  observed runs. The server only adds `received_at`; everything else is
 *  whatever the `claude` CLI's own hook payload carried, so this stays a
 *  loose bag of known-useful fields plus an index signature for the rest
 *  rather than a strict schema. */
export interface SubagentEvent {
  hook_event_name?: string;
  agent_id?: string;
  agent_type?: string;
  subagent_type?: string;
  last_assistant_message?: string;
  received_at?: string;
  [key: string]: unknown;
}

export function getSubagentEvents(
  slug: string,
  chatId: string,
): Promise<{ events: SubagentEvent[] }> {
  return api(
    `/api/agents/${encodeURIComponent(slug)}/chats/${encodeURIComponent(chatId)}/subagents`,
  );
}
