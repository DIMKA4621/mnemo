"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import {
  attachLink,
  createAgent,
  deleteAgent,
  detachLink,
  patchAgent,
  previewAgent,
  putAgentLaunch,
  putClaudeMd,
  updateLink,
  type AgentPreviewRequest,
  type AttachLinkRequest,
  type CreateAgentRequest,
  type LaunchConfig,
  type PatchAgentRequest,
  type UpdateLinkRequest,
} from "@/lib/api/agents";
import { createChat, deleteChat } from "@/lib/api/agentChats";
import type { CatalogCategory } from "@/lib/api/catalog";

/** No cache to invalidate — a dry-run inspection, not a write. */
export function usePreviewAgent() {
  return useMutation({
    mutationFn: (req: AgentPreviewRequest) => previewAgent(req),
  });
}

export function useCreateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateAgentRequest) => createAgent(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.agents.all }),
  });
}

export function useDeleteAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => deleteAgent(slug),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.agents.all }),
  });
}

export function usePutAgentLaunch() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, config }: { slug: string; config: LaunchConfig }) => putAgentLaunch(slug, config),
    onSuccess: (_result, { slug }) => {
      qc.invalidateQueries({ queryKey: queryKeys.agentLaunch.one(slug) });
      qc.invalidateQueries({ queryKey: queryKeys.agents.all });
    },
  });
}

/** MN-48 rename. `slug` never changes (`agent_registry.rename`) — the tree
 *  query and this agent's own cached entry both just refresh in place. */
export function usePatchAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, req }: { slug: string; req: PatchAgentRequest }) => patchAgent(slug, req),
    onSuccess: (updated) => {
      qc.invalidateQueries({ queryKey: queryKeys.agents.all });
      qc.setQueryData(queryKeys.agents.one(updated.slug), updated);
    },
  });
}

export function usePutClaudeMd() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, content }: { slug: string; content: string }) => putClaudeMd(slug, content),
    onSuccess: (_result, { slug }) => qc.invalidateQueries({ queryKey: queryKeys.agentClaudeMd.one(slug) }),
  });
}

/** Attach/update/detach all invalidate the catalog too — `used_by_count`
 *  (Реєстр's badge, Фаза A) is server-computed off exactly these links. */
export function useAttachLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      slug,
      category,
      req,
    }: {
      slug: string;
      category: CatalogCategory;
      req: AttachLinkRequest;
    }) => attachLink(slug, category, req),
    onSuccess: (_result, { slug }) => {
      qc.invalidateQueries({ queryKey: queryKeys.agentLinks.one(slug) });
      qc.invalidateQueries({ queryKey: queryKeys.catalog.all });
    },
  });
}

export function useUpdateLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      slug,
      category,
      entryId,
      req,
    }: {
      slug: string;
      category: CatalogCategory;
      entryId: string;
      req: UpdateLinkRequest;
    }) => updateLink(slug, category, entryId, req),
    onSuccess: (_result, { slug }) => {
      qc.invalidateQueries({ queryKey: queryKeys.agentLinks.one(slug) });
      qc.invalidateQueries({ queryKey: queryKeys.catalog.all });
    },
  });
}

export function useDetachLink() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      slug,
      category,
      entryId,
    }: {
      slug: string;
      category: CatalogCategory;
      entryId: string;
    }) => detachLink(slug, category, entryId),
    onSuccess: (_result, { slug }) => {
      qc.invalidateQueries({ queryKey: queryKeys.agentLinks.one(slug) });
      qc.invalidateQueries({ queryKey: queryKeys.catalog.all });
    },
  });
}

/** MN-44. Cheap on purpose (`POST /api/agents/{slug}/chats`'s own docstring):
 *  creates the record only, never spawns the real `claude` process — that
 *  happens lazily on the chat console's first WS connect. */
export function useCreateChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, title }: { slug: string; title?: string | null }) => createChat(slug, title),
    onSuccess: (_result, { slug }) => qc.invalidateQueries({ queryKey: queryKeys.agentChats.list(slug) }),
  });
}

/** Deleting a chat is the one action allowed to stop its live session
 *  (`api_delete_chat` -> `agent_runtime.stop_session`) — everything else in
 *  this feature (switching agents, switching chats, closing the tab) only
 *  ever closes the WebSocket, never the process. */
export function useDeleteChat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, chatId }: { slug: string; chatId: string }) => deleteChat(slug, chatId),
    onSuccess: (_result, { slug }) => qc.invalidateQueries({ queryKey: queryKeys.agentChats.list(slug) }),
  });
}
