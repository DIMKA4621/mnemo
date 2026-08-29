"use client";

import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import { getAgent, getAgentLaunch, getAgentLinks, getAgents, getClaudeMd } from "@/lib/api/agents";
import { getChats, getSubagents } from "@/lib/api/agentChats";

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

/** MN-43/MN-44. `AgentTreeRow` keeps this mounted for every agent row (not
 *  just expanded ones), so the tree's chat count is a real fact rather than
 *  a placeholder — see `AgentsHeader`'s own docstring for the same reasoning
 *  applied one level up. */
export function useAgentChats(slug: string) {
  return useQuery({
    queryKey: queryKeys.agentChats.list(slug),
    queryFn: async () => (await getChats(slug)).chats,
  });
}

/** MN-44. Read-only display of `.claude/agents/*.md` — fetched lazily, only
 *  while the agent-settings screen actually shows it. */
export function useAgentSubagents(slug: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.agentSubagents.one(slug ?? ""),
    queryFn: async () => (await getSubagents(slug as string)).subagents,
    enabled: !!slug && enabled,
  });
}
