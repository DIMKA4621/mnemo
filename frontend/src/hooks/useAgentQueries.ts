"use client";

import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import { getAgent, getAgentLaunch, getAgentLinks, getAgents, getClaudeMd } from "@/lib/api/agents";

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

export function useAgentLinks(slug: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.agentLinks.one(slug ?? ""),
    queryFn: () => getAgentLinks(slug as string),
    enabled: !!slug && enabled,
  });
}

export function useAgentClaudeMd(slug: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.agentClaudeMd.one(slug ?? ""),
    queryFn: () => getClaudeMd(slug as string),
    enabled: !!slug && enabled,
  });
}
