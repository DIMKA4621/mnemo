"use client";

import { useMemo } from "react";
import { useT } from "@/lib/i18n/hooks";
import type { SubagentEvent } from "@/lib/api/agentChats";

interface SubagentPanelProps {
  events: SubagentEvent[];
}

interface SubagentRun {
  agentId: string;
  agentType: string | null;
  completed: boolean;
  summary: string | null;
}

/** Groups a flat event log (one line per hook event — Start and Stop are
 *  never merged on disk, see `agent_registry.subagents_sidecar_path`) into
 *  one row per `agent_id`, most-recently-started first. A `SubagentStart`
 *  with no matching `SubagentStop` yet reads as "started, no completion
 *  signal yet" everywhere in this panel, never "running" — a crashed
 *  subagent simply never sends a Stop, so that guarantee doesn't exist
 *  (MN-45b, known accepted limitation). */
function buildRuns(events: SubagentEvent[]): SubagentRun[] {
  const byId = new Map<string, SubagentRun>();
  const order: string[] = [];
  for (const ev of events) {
    const id = typeof ev.agent_id === "string" ? ev.agent_id : null;
    if (!id) continue;
    if (!byId.has(id)) {
      byId.set(id, { agentId: id, agentType: null, completed: false, summary: null });
      order.push(id);
    }
    const run = byId.get(id)!;
    if (ev.hook_event_name === "SubagentStart") {
      const type = ev.agent_type ?? ev.subagent_type;
      if (typeof type === "string" && type) run.agentType = type;
    } else if (ev.hook_event_name === "SubagentStop") {
      run.completed = true;
      const msg = ev.last_assistant_message;
      if (typeof msg === "string" && msg.trim()) run.summary = msg.trim();
    }
  }
  return order
    .map((id) => byId.get(id)!)
    .reverse();
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/**
 * A compact, secondary strip of observed subagent runs for the open chat
 * (MN-45b) — NOT a re-implementation of anything the CLI's own TUI already
 * draws inside the terminal itself (Task-tool progress, tool calls, …).
 * Renders nothing when there is nothing to show, so an agent that never
 * spawns a subagent never pays for this panel's vertical space.
 */
export function SubagentPanel({ events }: SubagentPanelProps) {
  const t = useT();
  const runs = useMemo(() => buildRuns(events), [events]);

  if (runs.length === 0) return null;

  return (
    <div className="sa-panel">
      <span className="sa-panel-label">{t("agents.console.subagents.label")}</span>
      <div className="sa-panel-list">
        {runs.map((run) => (
          <span
            key={run.agentId}
            className={`sa-run${run.completed ? " is-done" : " is-pending"}`}
            title={run.summary ?? undefined}
          >
            <span className={`dot${run.completed ? "" : " busy"}`} />
            <span className="sa-run-type">{run.agentType ?? run.agentId.slice(0, 8)}</span>
            <span className="sa-run-status">
              {run.completed
                ? t("agents.console.subagents.done")
                : t("agents.console.subagents.pending")}
            </span>
            {run.summary && <span className="sa-run-summary">{truncate(run.summary, 80)}</span>}
          </span>
        ))}
      </div>
    </div>
  );
}
