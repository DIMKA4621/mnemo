"use client";

import { useEffect } from "react";
import { Segmented } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { InlineNote, useInlineNote } from "@/components/common/InlineNote";
import type { AutostartResult } from "@/lib/api/settings";
import type { FieldSaveResult } from "./GeneralSection";

interface AutostartFieldProps {
  autostart: AutostartResult | null;
  error: string | null;
  want: boolean | null;
  onChoose: (want: boolean | null) => void;
  busy: boolean;
  saveResult: FieldSaveResult | null;
}

/**
 * Start at logon. Selecting picks, it does not apply — the shared «Зберегти»
 * in `SettingsTabs`'s footer does. Registering a scheduled task changes what
 * happens at the NEXT logon and touches nothing running, so the page it is
 * served from survives the change either way (unlike shutdown).
 */
export function AutostartField({ autostart, error, want, onChoose, busy, saveResult }: AutostartFieldProps) {
  const t = useT();
  const [note, setNote] = useInlineNote();

  useEffect(() => {
    if (saveResult) setNote(saveResult.message);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saveResult]);

  if (!autostart) {
    return (
      <div className="set-field">
        <span className="set-label">{t("settings.general.autostart.label")}</span>
        <p className="set-note">{error || t("settings.general.autostart.notFetched")}</p>
      </div>
    );
  }

  if (!autostart.supported) {
    return (
      <div className="set-field">
        <span className="set-label">{t("settings.general.autostart.label")}</span>
        <p className="set-note">{t("settings.general.autostart.unsupported")}</p>
      </div>
    );
  }

  const chosen = want ?? autostart.enabled;
  const named = autostart.name ? t("settings.general.autostart.namedSuffix", { name: autostart.name }) : "";

  function choose(next: boolean) {
    if (busy || !autostart) return;
    onChoose(next === autostart.enabled ? null : next);
  }

  return (
    <div className="set-field">
      <span className="set-label">{t("settings.general.autostart.label")}</span>
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
      <p className="set-note">{t("settings.general.autostart.note", { mechanism: autostart.mechanism || "—", named })}</p>
      {chosen !== autostart.enabled && (
        <p className="set-override">
          {t("settings.notSavedToggle", { state: t(autostart.enabled ? "settings.state.on" : "settings.state.off") })}
        </p>
      )}
      <InlineNote text={note} tone={saveResult?.ok === false ? "error" : "success"} />
    </div>
  );
}
