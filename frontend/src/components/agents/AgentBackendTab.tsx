"use client";

import { useEffect, useState } from "react";
import { useT } from "@/lib/i18n/hooks";
import { usePutAgentLaunch } from "@/hooks/useAgentMutations";
import { LaunchConfigFields } from "./LaunchConfigFields";
import { ApiError } from "@/lib/api/fetcher";
import type { AgentSectionController } from "./AgentSettings";
import type { AgentInfo, LaunchConfig } from "@/lib/api/agents";

interface AgentBackendTabProps {
  agent: AgentInfo;
  onController: (controller: AgentSectionController | null) => void;
}

function buildConfig(mode: "standard" | "custom", host: string, port: number | null, model: string): LaunchConfig {
  if (mode !== "custom") return { mode: "standard" };
  return { mode: "custom", host: host.trim(), port: port as number, ...(model.trim() ? { model: model.trim() } : {}) };
}

/**
 * Launch backend (`standard`/`custom`), same fields the create-agent
 * wizard's step 2 uses (`LaunchConfigFields`) — MN-42 Фаза C, ported from
 * the mockup's `renderAgsBackend`. `agent.launch` (already on `AgentInfo`
 * from `useAgents()`) seeds this tab directly rather than a fresh
 * `GET /api/agents/{slug}/launch` fetch — the tree's own query already
 * carries it, and `usePutAgentLaunch`'s success invalidates that query, so
 * a save here is visible everywhere else without a second round trip.
 * `GET .../launch` (`useAgentLaunch`) is unused here for that reason, not
 * because it's redundant infrastructure — it stays for a caller that opens
 * this tab without the tree's list already loaded.
 */
export function AgentBackendTab({ agent, onController }: AgentBackendTabProps) {
  const t = useT();
  const putLaunchMutation = usePutAgentLaunch();

  const launchError = "error" in agent.launch ? agent.launch.error : null;
  const loadedLaunch: LaunchConfig = "error" in agent.launch ? { mode: "standard" } : agent.launch;

  const [mode, setMode] = useState<"standard" | "custom">(loadedLaunch.mode);
  const [host, setHost] = useState(loadedLaunch.mode === "custom" ? loadedLaunch.host : "127.0.0.1");
  const [port, setPort] = useState<number | null>(loadedLaunch.mode === "custom" ? loadedLaunch.port : 8787);
  const [model, setModel] = useState(loadedLaunch.mode === "custom" ? (loadedLaunch.model ?? "") : "");
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  const canSubmit = mode === "standard" || (host.trim() !== "" && port !== null && port >= 1 && port <= 65535);
  const hasPendingChange =
    canSubmit && JSON.stringify(buildConfig(mode, host, port, model)) !== JSON.stringify(loadedLaunch);
  const busy = putLaunchMutation.isPending;

  async function submit() {
    if (!hasPendingChange || busy) return;
    setResult(null);
    try {
      await putLaunchMutation.mutateAsync({ slug: agent.slug, config: buildConfig(mode, host, port, model) });
      setResult({ ok: true, message: t("agents.settings.backend.savedNote") });
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingChange, busy, mode, host, port, model]);

  return (
    <>
      {launchError && <p className="modal-error">{t("agents.settings.backend.invalidNote", { error: launchError })}</p>}
      <LaunchConfigFields
        mode={mode}
        onModeChange={setMode}
        host={host}
        onHostChange={setHost}
        port={port}
        onPortChange={setPort}
        model={model}
        onModelChange={setModel}
      />
      <p className="wiz-hint" style={{ marginTop: 10 }}>{t("agents.settings.backend.savedHint")}</p>
      {result && (
        <p className={result.ok ? "wiz-hint" : "modal-error"} style={result.ok ? { color: "var(--ok)" } : undefined}>
          {result.message}
        </p>
      )}
    </>
  );
}
