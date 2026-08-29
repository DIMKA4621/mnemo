"use client";

import { useT } from "@/lib/i18n/hooks";
import { useAgents } from "@/hooks/useAgentQueries";
import { AgentTreeRow } from "./AgentTreeRow";

interface AgentTreeProps {
  expanded: Set<string>;
  selectedSlug: string | null;
  onToggle: (slug: string) => void;
  onOpenSettings: (slug: string) => void;
}

/** Left pane's whole content — the agent list, master-detail's tree side. */
export function AgentTree({ expanded, selectedSlug, onToggle, onOpenSettings }: AgentTreeProps) {
  const t = useT();
  const query = useAgents();
  const agents = query.data ?? [];

  if (query.isLoading) {
    return <p className="empty-hint">{t("agents.tree.loading")}</p>;
  }

  if (!agents.length) {
    return <p className="empty-hint">{t("agents.tree.empty")}</p>;
  }

  return (
    <>
      {agents.map((agent) => (
        <AgentTreeRow
          key={agent.slug}
          agent={agent}
          expanded={expanded.has(agent.slug)}
          selected={agent.slug === selectedSlug}
          onToggle={() => onToggle(agent.slug)}
          onOpenSettings={() => onOpenSettings(agent.slug)}
        />
      ))}
    </>
  );
}
