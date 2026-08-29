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
