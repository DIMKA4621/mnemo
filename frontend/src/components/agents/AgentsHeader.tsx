"use client";

import { createPortal } from "react-dom";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useAgents } from "@/hooks/useAgentQueries";
import { usePageHeaderSlotStore } from "@/lib/store/page-header-slot";

interface AgentsHeaderProps {
  onAdd: () => void;
}

/**
 * Portals into the shell's persistent `Topbar`, same mechanism as
 * `MemoryPageHeader`/`RegistryTabs` — page title, the agent/chat count, and
 * "＋ Add agent" all live in that one row. Chat count is always 0 for now
 * (see `AgentTreeRow`'s docstring) but still rendered, matching the
 * mockup's `renderTopLeft` — a real fact ("no chats exist yet"), not a
 * placeholder to hide.
 */
export function AgentsHeader({ onAdd }: AgentsHeaderProps) {
  const t = useT();
  const agentsQuery = useAgents();
  const slot = usePageHeaderSlotStore((s) => s.slot);

  const agents = agentsQuery.data ?? [];

  if (!slot) return null;

  return createPortal(
    <>
      <span className="page-title">{t("shell.nav.agents")}</span>
      <span className="page-sub">{t("agents.header.count", { n: agents.length, c: 0 })}</span>
      <div style={{ flex: 1 }} />
      <Button size="small" title={t("agents.header.addTitle")} onClick={onAdd}>
        {t("agents.header.add")}
      </Button>
    </>,
    slot,
  );
}
