"use client";

import { useEffect, useState } from "react";
import { Input } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useAgentClaudeMd } from "@/hooks/useAgentQueries";
import { usePatchAgent, usePutClaudeMd } from "@/hooks/useAgentMutations";
import { ApiError } from "@/lib/api/fetcher";
import type { AgentSectionController } from "./AgentSettings";
import type { AgentInfo } from "@/lib/api/agents";

const { TextArea } = Input;

interface AgentGeneralTabProps {
  agent: AgentInfo;
  onController: (controller: AgentSectionController | null) => void;
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
export function AgentGeneralTab({ agent, onController }: AgentGeneralTabProps) {
  const t = useT();
  const claudeMdQuery = useAgentClaudeMd(agent.slug, true);
  const patchAgentMutation = usePatchAgent();
  const putClaudeMdMutation = usePutClaudeMd();

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
    </>
  );
}
