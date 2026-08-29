"use client";

import { useEffect, useState } from "react";
import { useT } from "@/lib/i18n/hooks";
import { useTokenStore } from "@/lib/store/token";
import { useSettings, useAutostart } from "@/hooks/useSettingsQueries";
import { useSetAutostart, usePutSettings } from "@/hooks/useSettingsMutations";
import { useStatus } from "@/hooks/useMemoryQueries";
import { ApiError } from "@/lib/api/fetcher";
import { AutostartField } from "./AutostartField";
import { AutoUpdateField } from "./AutoUpdateField";
import { RequireLoginField } from "./RequireLoginField";
import { ServiceStatusStats } from "./ServiceStatusStats";
import type { SectionController } from "./SettingsTabs";

export interface FieldSaveResult {
  ok: boolean;
  message: string;
}

interface GeneralSectionProps {
  onController: (controller: SectionController | null) => void;
}

/**
 * Autostart / auto-update / require-login: three independently-editable
 * toggles sharing one «Зберегти» click (`SettingsTabs`'s footer, registered
 * via `onController`) — same shape as the vanilla console's `submitGeneral`.
 * Each field owns its own pending "want" comparison against the stored
 * value and its own save verdict (`AutostartField`/`AutoUpdateField`/
 * `RequireLoginField`, each with its own `useInlineNote()`) so that saving
 * one does not stomp another's still-visible confirmation — this section
 * only decides WHICH of the (up to three) requests to fire and hands each
 * field back its own outcome.
 */
export function GeneralSection({ onController }: GeneralSectionProps) {
  const t = useT();
  const settingsQuery = useSettings();
  const autostartQuery = useAutostart(true);
  const statusQuery = useStatus();
  const setAutostartMutation = useSetAutostart();
  const putSettingsMutation = usePutSettings();

  const [autostartWant, setAutostartWant] = useState<boolean | null>(null);
  const [autoUpdateWant, setAutoUpdateWant] = useState<boolean | null>(null);
  const [requireLoginWant, setRequireLoginWant] = useState<boolean | null>(null);
  const [autostartResult, setAutostartResult] = useState<FieldSaveResult | null>(null);
  const [autoUpdateResult, setAutoUpdateResult] = useState<FieldSaveResult | null>(null);
  const [requireLoginResult, setRequireLoginResult] = useState<FieldSaveResult | null>(null);
  const [requireLoginToken, setRequireLoginToken] = useState<string | null>(null);

  const busy = setAutostartMutation.isPending || putSettingsMutation.isPending;
  const hasPendingChange = autostartWant != null || autoUpdateWant != null || requireLoginWant != null;

  async function submit() {
    // Dropped rather than reset to `null` up front: a field's OWN want must
    // survive this function even if its own request fails, but its
    // previous verdict must not linger under a submit that has not
    // resolved yet.
    setAutostartResult(null);
    setAutoUpdateResult(null);
    setRequireLoginResult(null);

    if (autostartWant != null) {
      try {
        const result = await setAutostartMutation.mutateAsync(autostartWant);
        setAutostartWant(null);
        setAutostartResult({
          ok: true,
          message: t(result.enabled ? "settings.general.autostart.savedOn" : "settings.general.autostart.savedOff"),
        });
      } catch (err) {
        setAutostartResult({ ok: false, message: err instanceof Error ? err.message : String(err) });
      }
    }

    if (autoUpdateWant != null) {
      try {
        await putSettingsMutation.mutateAsync({ auto_update: autoUpdateWant });
        const want = autoUpdateWant;
        setAutoUpdateWant(null);
        setAutoUpdateResult({
          ok: true,
          message: t(want ? "settings.general.autoUpdate.savedOn" : "settings.general.autoUpdate.savedOff"),
        });
      } catch (err) {
        setAutoUpdateResult({ ok: false, message: err instanceof Error ? err.message : String(err) });
      }
    }

    if (requireLoginWant != null) {
      try {
        const data = await putSettingsMutation.mutateAsync({ require_login: requireLoginWant });
        const want = requireLoginWant;
        setRequireLoginWant(null);
        setRequireLoginResult({
          ok: true,
          message: t(want ? "settings.general.requireLogin.savedOn" : "settings.general.requireLogin.savedOff"),
        });
        // Rides in this exact response the moment the save turns the gate
        // on — never echoed by a plain GET afterwards, so it must be
        // adopted right here or it is gone. Adopted into this session's own
        // credential immediately too: otherwise the very next `/api`
        // request (or a WS reconnect) would 401 and slam the gate shut on
        // the same screen that just handed the token out.
        if (data.service_token) {
          setRequireLoginToken(data.service_token);
          useTokenStore.getState().setToken(data.service_token);
        }
      } catch (err) {
        setRequireLoginResult({
          ok: false,
          message: err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err),
        });
      }
    }
  }

  useEffect(() => {
    onController({ hasPendingChange, busy, submit });
    return () => onController(null);
    // `submit` is recreated every render (it closes over this render's want
    // state) — the controller must always hold the latest one, so it is a
    // dependency rather than something to memoize away.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPendingChange, busy, autostartWant, autoUpdateWant, requireLoginWant]);

  return (
    <>
      <AutostartField
        autostart={autostartQuery.data ?? null}
        error={autostartQuery.error instanceof Error ? autostartQuery.error.message : null}
        want={autostartWant}
        onChoose={setAutostartWant}
        busy={busy}
        saveResult={autostartResult}
      />
      <RequireLoginField
        settings={settingsQuery.data ?? null}
        want={requireLoginWant}
        onChoose={setRequireLoginWant}
        busy={busy}
        saveResult={requireLoginResult}
        token={requireLoginToken}
      />
      <AutoUpdateField
        settings={settingsQuery.data ?? null}
        want={autoUpdateWant}
        onChoose={setAutoUpdateWant}
        busy={busy}
        saveResult={autoUpdateResult}
      />

      {!statusQuery.data ? (
        <p className="empty-hint">{t("settings.general.serviceNotLoaded")}</p>
      ) : (
        <>
          <div className="set-divider" />
          <ServiceStatusStats status={statusQuery.data} />
        </>
      )}

      <div className="set-divider" />
      <div className="set-field">
        <span className="set-label">{t("settings.general.aboutLabel")}</span>
        <a className="set-link" href="https://github.com/DIMKA4621/mnemo" target="_blank" rel="noopener noreferrer">
          GitHub
        </a>
      </div>
    </>
  );
}
