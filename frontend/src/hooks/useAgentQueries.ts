"use client";

import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import { getAgent, getAgentLaunch, getAgents } from "@/lib/api/agents";

export function useAgents() {
  return useQuery({
    queryKey: queryKeys.agents.all,
    queryFn: async () => (await getAgents()).agents,
  });
}

export function useAgent(slug: string | null) {
  return useQuery({
    queryKey: queryKeys.agents.one(slug ?? ""),
    queryFn: () => getAgent(slug as string),
    enabled: !!slug,
  });
}

export function useAgentLaunch(slug: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.agentLaunch.one(slug ?? ""),
    queryFn: () => getAgentLaunch(slug as string),
    enabled: !!slug && enabled,
  });
}
