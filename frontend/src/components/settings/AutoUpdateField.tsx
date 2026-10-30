"use client";

import { useEffect, useState } from "react";
import { Segmented, Button } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { useT } from "@/lib/i18n/hooks";
import { InlineNote, useInlineNote } from "@/components/common/InlineNote";
import { useCheckForUpdate } from "@/hooks/useSettingsMutations";
import { getUpdateStatus, type SettingsResult } from "@/lib/api/settings";
import { queryKeys } from "@/lib/query/keys";
import { useUpdateModalStore } from "@/lib/store/update-modal";
import type { FieldSaveResult } from "./GeneralSection";

interface AutoUpdateFieldProps {
  settings: SettingsResult | null;
  want: boolean | null;
  onChoose: (want: boolean | null) => void;
  busy: boolean;
  saveResult: FieldSaveResult | null;
}

/**
 * `PUT /api/settings`'s `auto_update` key — same Save-gated toggle idiom as
 * `AutostartField`, plus the manual «Перевірити оновлення» trigger
 * (`POST /api/update/check`), the same check the sidebar banner's own
 * background tick runs. Never itself opens the auto-pending countdown —
 * it only refreshes `latest_tag`/`update_available`; clicking its own
 * "available" result opens the confirm dialog (`UpdateModal`), same as the
 * sidebar banner does.
 */
export function AutoUpdateField({ settings, want, onChoose, busy, saveResult }: AutoUpdateFieldProps) {
  const t = useT();
  const qc = useQueryClient();
  const [note, setNote] = useInlineNote();
  const checkMutation = useCheckForUpdate();
  const [checkResult, setCheckResult] = useState<{ available: boolean; text: string } | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);

  useEffect(() => {
    if (saveResult) setNote(saveResult.message);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saveResult]);

  // Same auto-expiry the vanilla console gave every settings verdict —
  // this one just is not itself an `InlineNote`, since it renders as a
  // clickable chip rather than a plain note.
  useEffect(() => {
    if (!checkResult) return;
    const timer = setTimeout(() => setCheckResult(null), 5000);
    return () => clearTimeout(timer);
  }, [checkResult]);

  const autoUpdateItem = settings?.settings.auto_update;
  const stored = autoUpdateItem?.value ?? true;
  const chosen = want ?? stored;

  function choose(next: boolean) {
    if (busy) return;
    onChoose(next === stored ? null : next);
  }

  async function runCheck() {
    setCheckError(null);
    setCheckResult(null);
    try {
      const result = await checkMutation.mutateAsync();
      await qc.fetchQuery({ queryKey: queryKeys.updateStatus.all, queryFn: getUpdateStatus });
      if (result.error) {
        setCheckError(result.error);
        return;
      }
      setCheckResult({
        available: result.update_available,
        text: result.update_available
          ? t("update.banner.available", { tag: result.latest_tag ?? "—" })
          : t("settings.general.autoUpdate.upToDate"),
      });
    } catch (err) {
      setCheckError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <div className="set-divider" />
      <div className="set-field">
        <span className="set-label">{t("settings.general.autoUpdate.label")}</span>
        <Segmented
          className="set-toggle"
          value={chosen ? "on" : "off"}
          disabled={busy}
          onChange={(v) => choose(v === "on")}
          options={[
            { label: t("settings.toggle.on"), value: "on" },
            { label: t("settings.toggle.off"), value: "off" },
          ]}
        />
        <p className="set-note">{t("settings.general.autoUpdate.note")}</p>
        {autoUpdateItem?.overridden && (
          <p className="set-override">{t("settings.overrideNote", { var: autoUpdateItem.env_var })}</p>
        )}
        {chosen !== stored && (
          <p className="set-override">
            {t("settings.notSavedToggle", { state: t(stored ? "settings.state.on" : "settings.state.off") })}
          </p>
        )}
        <InlineNote text={note} tone={saveResult?.ok === false ? "error" : "success"} />
      </div>

      <div className="maint-head">
        <Button size="small" loading={checkMutation.isPending} disabled={checkMutation.isPending} onClick={runCheck}>
          {checkMutation.isPending ? t("settings.general.autoUpdate.checking") : t("settings.general.autoUpdate.checkBtn")}
        </Button>
      </div>
      {checkError && <p className="modal-error">{checkError}</p>}
      {checkResult &&
        (checkResult.available ? (
          <button
            type="button"
            className="upd-check-note is-available"
            onClick={() => useUpdateModalStore.getState().setPhase("confirm")}
          >
            {checkResult.text}
          </button>
        ) : (
          <p className="upd-check-note is-current">{checkResult.text}</p>
        ))}
    </>
  );
}
