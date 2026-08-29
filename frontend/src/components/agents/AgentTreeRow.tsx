"use client";

import { useT } from "@/lib/i18n/hooks";
import type { AgentInfo } from "@/lib/api/agents";

interface AgentTreeRowProps {
  agent: AgentInfo;
  expanded: boolean;
  selected: boolean;
  onToggle: () => void;
  onOpenSettings: () => void;
}

/**
 * One agent row plus its (always-empty, for now) chats strip. Ported layout
 * from the mockup's `renderTree` (`.claude/scratch/agents-page-mockup/app.js`)
 * with Phase B's scope narrowed by lead decision (Jira MN-42, 29.08.2026):
 * chat data doesn't exist yet (MN-43 isn't built), so every agent's chat list
 * is always the empty state — no "+ Новий чат" button (nothing to post to,
 * so it's removed rather than disabled) and no per-chat "thinking" dot.
 *
 * The mockup's live-activity pulse dot is dropped entirely rather than
 * always rendered "not generating" — there is no live source for it in this
 * phase, and a dot that can never turn on is a UI element promising a state
 * change that will never come from here.
 *
 * The ⚙ gear button opens the agent-settings screen (Фаза C). It sits
 * inside the row's own click target, so it stops propagation before firing
 * `onOpenSettings` — otherwise clicking it would also toggle the chat strip,
 * same nesting the mockup's own CSS comment calls out (`.ag-agent-gear`:
 * "a real, separately clickable button").
 *
 * A single click anywhere else in the row both toggles the chat strip AND
 * selects the agent for the workspace pane — there's no separate control to
 * click into, since there are no chats to open instead.
 */
export function AgentTreeRow({ agent, expanded, selected, onToggle, onOpenSettings }: AgentTreeRowProps) {
  const t = useT();

  return (
    <div className="ag-agent">
      <div
        className={`ag-agent-row${selected ? " is-selected" : ""}`}
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(ev) => {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            onToggle();
          }
        }}
      >
        <span className="ag-agent-title">
          <span className="ag-agent-name">{agent.name}</span>
        </span>
        <span className="ag-agent-count">0</span>
        <button
          type="button"
          className="ag-agent-gear"
          title={t("agents.tree.settingsTitle")}
          aria-label={t("agents.tree.settingsTitle")}
          onClick={(ev) => {
            ev.stopPropagation();
            onOpenSettings();
          }}
        >
          ⚙
        </button>
        <span className="ag-twisty" aria-hidden="true">{expanded ? "▾" : "▸"}</span>
      </div>
      {expanded && (
        <div className="ag-chats">
          <div className="ag-no-chats">{t("agents.tree.noChats")}</div>
        </div>
      )}
    </div>
  );
}
