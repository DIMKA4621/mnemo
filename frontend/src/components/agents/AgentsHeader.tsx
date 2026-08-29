"use client";

import { createPortal } from "react-dom";
import { useQueries } from "@tanstack/react-query";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useAgents } from "@/hooks/useAgentQueries";
import { usePageHeaderSlotStore } from "@/lib/store/page-header-slot";
import { queryKeys } from "@/lib/query/keys";
import { getChats } from "@/lib/api/agentChats";

interface AgentsHeaderProps {
  onAdd: () => void;
}

/**
 * Portals into the shell's persistent `Topbar`, same mechanism as
 * `MemoryPageHeader`/`RegistryTabs` — page title, the agent/chat count, and
 * "＋ Add agent" all live in that one row. The chat count (MN-44) sums every
 * agent's own `useAgentChats` query — same `queryKeys.agentChats.list(slug)`
 * key `AgentTreeRow` already populates, so this never fires an extra
 * network request beyond what the tree itself needed.
 */
export function AgentsHeader({ onAdd }: AgentsHeaderProps) {
  const t = useT();
  const agentsQuery = useAgents();
  const slot = usePageHeaderSlotStore((s) => s.slot);

  const agents = agentsQuery.data ?? [];
  const chatQueries = useQueries({
    queries: agents.map((agent) => ({
      queryKey: queryKeys.agentChats.list(agent.slug),
      queryFn: async () => (await getChats(agent.slug)).chats,
    })),
  });
  const totalChats = chatQueries.reduce((sum, q) => sum + (q.data?.length ?? 0), 0);

  if (!slot) return null;

  return createPortal(
    <>
      <span className="page-title">{t("shell.nav.agents")}</span>
      <span className="page-sub">{t("agents.header.count", { n: agents.length, c: totalChats })}</span>
      <div style={{ flex: 1 }} />
      <Button size="small" title={t("agents.header.addTitle")} onClick={onAdd}>
        {t("agents.header.add")}
      </Button>
    </>,
    slot,
  );
}
