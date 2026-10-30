"use client";

import { useEffect, useState } from "react";
import { Button, Input } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useAgentClaudeMd, useAgentSubagents } from "@/hooks/useAgentQueries";
import { usePatchAgent, usePutClaudeMd, useLaunchSubagent } from "@/hooks/useAgentMutations";
import { ApiError } from "@/lib/api/fetcher";
import type { AgentSectionController } from "./AgentSettings";
import type { AgentInfo } from "@/lib/api/agents";
import type { ChatInfo } from "@/lib/api/agentChats";

const { TextArea } = Input;

interface AgentGeneralTabProps {
  agent: AgentInfo;
  onController: (controller: AgentSectionController | null) => void;
  /** MN-45 Phase C. Fires once a subagent definition has actually become a
   *  new top-level agent + chat (`POST .../subagents/{name}/launch`) — the
   *  page decides what "open it" means (`AgentsPage.tsx`'s `selectChat`),
   *  this tab only reports that it happened, same `(agent, message)` shape
   *  `CreateAgentWizard`'s `onCreated` already uses. */
  onLaunched?: (agent: AgentInfo, chat: ChatInfo, message: string) => void;
}

/**
 * Name (rename, MN-48 `PATCH /api/agents/{slug}`) + CLAUDE.md (MN-48
 * `GET`/`PUT /api/agents/{slug}/claude-md`), ported fields from the
 * mockup's `renderAgsGeneral`. No client-side name-uniqueness check —
 * `agent_registry.rename`'s own docstring: `Agent.name` was never unique
 * to begin with, only `slug` is, and a rename is not the place to start
 * enforcing that.
 *
 * Pending-change comparisons are against the live `agent`/query props
 * directly rather than a snapshot taken at mount — after this tab's own
 * save invalidates and refetches, the props catch up to what was just
 * typed and the comparison naturally goes false again, no bookkeeping
 * needed for the common case.
 */
export function AgentGeneralTab({ agent, onController, onLaunched }: AgentGeneralTabProps) {
  const t = useT();
  const claudeMdQuery = useAgentClaudeMd(agent.slug, true);
  const subagentsQuery = useAgentSubagents(agent.slug, true);
  const patchAgentMutation = usePatchAgent();
  const putClaudeMdMutation = usePutClaudeMd();
  const launchSubagentMutation = useLaunchSubagent();
  const [launchingName, setLaunchingName] = useState<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const [name, setName] = useState(agent.name);
  const [claudeMd, setClaudeMd] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  // Adopt the fetched content into local editable state once, the
  // react.dev-blessed "adjust state during render" shape rather than an
  // Effect (`react-hooks/set-state-in-effect` flags a synchronous setState
  // in an Effect body; this conditional runs during render and React
  // re-renders immediately before committing, so there's no extra paint).
  // `syncedContent` guards it from re-firing on every render once loaded.
  const [syncedContent, setSyncedContent] = useState<string | undefined>(undefined);
  if (claudeMdQuery.data !== undefined && claudeMdQuery.data.content !== syncedContent) {
    setSyncedContent(claudeMdQuery.data.content);
    setClaudeMd(claudeMdQuery.data.content);
  }
  const loaded = syncedContent !== undefined;

  const wantName = name.trim() !== "" && name.trim() !== agent.name ? name.trim() : null;
  const wantClaudeMd = loaded && claudeMd !== null && claudeMd !== claudeMdQuery.data?.content ? claudeMd : null;
  const hasPendingChange = wantName !== null || wantClaudeMd !== null;
  const busy = patchAgentMutation.isPending || putClaudeMdMutation.isPending;

  async function submit() {
    if (!hasPendingChange || busy) return;
    setResult(null);
    try {
      if (wantName !== null) {
        await patchAgentMutation.mutateAsync({ slug: agent.slug, req: { name: wantName } });
      }
      if (wantClaudeMd !== null) {
        await putClaudeMdMutation.mutateAsync({ slug: agent.slug, content: wantClaudeMd });
      }
      setResult({ ok: true, message: t("agents.settings.general.savedNote") });
    } catch (err) {
      setResult({
        ok: false,
        message: err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err),
      });
    }
  }

  useEffect(() => {
    onController({ hasPendingChange, busy, submit });
    return () => onController(null);
    // `submit` closes over this render's `wantName`/`wantClaudeMd` — always
    // needs to be the latest one, hence the dependency rather than memoizing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingChange, busy, wantName, wantClaudeMd]);

  async function handleLaunch(subagentName: string) {
    setLaunchingName(subagentName);
    setLaunchError(null);
    try {
      const { agent: newAgent, chat } = await launchSubagentMutation.mutateAsync({
        slug: agent.slug,
        name: subagentName,
      });
      onLaunched?.(newAgent, chat, t("agents.settings.general.subagentLaunchedNote", { name: newAgent.name }));
    } catch (err) {
      setLaunchError(
        t("agents.settings.general.subagentLaunchError", {
          name: subagentName,
          message: err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err),
        }),
      );
    } finally {
      setLaunchingName(null);
    }
  }

  return (
    <>
      <label className="wiz-field-label" htmlFor="ags-name">
        {t("agents.wizard.nameLabel")}
      </label>
      <Input id="ags-name" value={name} onChange={(e) => setName(e.target.value)} />

      <label className="wiz-field-label" htmlFor="ags-claude" style={{ marginTop: 10 }}>
        {t("agents.wizard.claudeMdLabel")}
      </label>
      {!loaded ? (
        <p className="empty-hint">{t("agents.settings.loading")}</p>
      ) : (
        <TextArea
          id="ags-claude"
          className="reg-code"
          rows={12}
          value={claudeMd ?? ""}
          onChange={(e) => setClaudeMd(e.target.value)}
        />
      )}

      {result && (
        <p className={result.ok ? "wiz-hint" : "modal-error"} style={result.ok ? { color: "var(--ok)" } : undefined}>
          {result.message}
        </p>
      )}

      {/* `.claude/agents/*.md` this agent's folder carries (MN-44), each with
          a MN-45 Phase C action that promotes it into a brand-new top-level
          agent + chat. */}
      <h2 style={{ marginTop: 18 }}>{t("agents.settings.general.subagentsTitle")}</h2>
      {subagentsQuery.isLoading && <p className="empty-hint">{t("agents.settings.loading")}</p>}
      {!subagentsQuery.isLoading && (subagentsQuery.data ?? []).length === 0 && (
        <p className="empty-hint">{t("agents.settings.general.subagentsEmpty")}</p>
      )}
      {(subagentsQuery.data ?? []).length > 0 && (
        <div className="pick-list">
          {(subagentsQuery.data ?? []).map((sub) => (
            <div key={sub.name} className="pick-row" style={{ cursor: "default" }}>
              <div className="pick-row-text">
                <div className="pick-row-name">{sub.name}</div>
                {sub.description && <div className="pick-row-meta">{sub.description}</div>}
              </div>
              <Button
                size="small"
                onClick={() => handleLaunch(sub.name)}
                loading={launchingName === sub.name}
                disabled={launchingName !== null && launchingName !== sub.name}
              >
                {t("agents.settings.general.subagentLaunchBtn")}
              </Button>
            </div>
          ))}
        </div>
      )}
      {launchError && <p className="modal-error">{launchError}</p>}
    </>
  );
}
