"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import {
  createAgent,
  deleteAgent,
  previewAgent,
  putAgentLaunch,
  type AgentPreviewRequest,
  type CreateAgentRequest,
  type LaunchConfig,
} from "@/lib/api/agents";

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
