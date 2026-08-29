"use client";

import { useEffect, useState } from "react";
import { AgentsHeader } from "./AgentsHeader";
import { AgentTree } from "./AgentTree";
import { AgentWorkspaceStub } from "./AgentWorkspaceStub";
import { CreateAgentWizard } from "./CreateAgentWizard";
import { PaneResizer } from "@/components/common/PaneResizer";
import { useInlineNote, InlineNote } from "@/components/common/InlineNote";
import { useAgentsPaneWidthStore } from "@/lib/store/agents-pane-width";
import { useAgents } from "@/hooks/useAgentQueries";
import type { AgentInfo } from "@/lib/api/agents";
import "./agents.css";
import "@/components/common/dialogs.css";

/**
 * Агенти: дерево агент→чати (tree) + workspace, ported layout from the
 * live-verified mockup's `layout-agents`
 * (`.claude/scratch/agents-page-mockup/index.html`). No sub-nav/"Чати" tab —
 * this is the page's one and only content, same as every other route.
 */
export default function AgentsPage() {
  const width = useAgentsPaneWidthStore((s) => s.width);
  const hydrateWidth = useAgentsPaneWidthStore((s) => s.hydrate);
  const beginDrag = useAgentsPaneWidthStore((s) => s.beginDrag);
  const applyDrag = useAgentsPaneWidthStore((s) => s.applyDrag);
  const commitDrag = useAgentsPaneWidthStore((s) => s.commitDrag);

  const agentsQuery = useAgents();

  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [note, setNote] = useInlineNote();

  useEffect(() => {
    hydrateWidth();
  }, [hydrateWidth]);

  function toggleAgent(slug: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
    setSelectedSlug(slug);
  }

  function handleCreated(agent: AgentInfo, message: string) {
    setExpanded((prev) => new Set(prev).add(agent.slug));
    setSelectedSlug(agent.slug);
    setNote(message);
  }

  const selectedAgent: AgentInfo | null = agentsQuery.data?.find((a) => a.slug === selectedSlug) ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <AgentsHeader onAdd={() => setWizardOpen(true)} />
      <div className="ag-layout" style={{ gridTemplateColumns: `${width}px 6px minmax(0, 1fr)` }}>
        <div className="ag-tree-pane">
          <AgentTree expanded={expanded} selectedSlug={selectedSlug} onToggle={toggleAgent} />
        </div>
        <PaneResizer onStart={beginDrag} onDrag={applyDrag} onCommit={commitDrag} />
        <div className="ag-workspace">
          <AgentWorkspaceStub agent={selectedAgent} />
        </div>
      </div>
      <CreateAgentWizard
        key={wizardOpen ? "wizard-open" : "wizard-closed"}
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={handleCreated}
      />
      <InlineNote text={note} tone="success" />
    </div>
  );
}
