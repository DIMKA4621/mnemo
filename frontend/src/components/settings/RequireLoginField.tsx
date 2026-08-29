"use client";

import { useEffect, useState } from "react";
import { Segmented, Input, Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { InlineNote, useInlineNote } from "@/components/common/InlineNote";
import { copyText } from "@/lib/memory/tokenSnippets";
import type { SettingsResult } from "@/lib/api/settings";
import type { FieldSaveResult } from "./GeneralSection";

function maskToken(value: string): string {
  return "•".repeat(value.length);
}

interface RequireLoginFieldProps {
  settings: SettingsResult | null;
  want: boolean | null;
  onChoose: (want: boolean | null) => void;
  busy: boolean;
  saveResult: FieldSaveResult | null;
  /** The freshly-minted service token, only present the instant a save just
   *  turned the gate on — a one-time reveal (MN-19), never echoed by a
   *  later `GET /api/settings`. Cleared on navigating away from this tab
   *  (`SettingsTabs` remounts this section on tab change). */
  token: string | null;
}

/**
 * Whether `/api` (console + CLI) requires a token — same Save-gated toggle
 * idiom as `AutostartField`/`AutoUpdateField`, and part of the same
 * `PUT /api/settings` document, so it carries an env override the same way.
 */
export function RequireLoginField({ settings, want, onChoose, busy, saveResult, token }: RequireLoginFieldProps) {
  const t = useT();
  const [note, setNote] = useInlineNote();
  const [revealed, setRevealed] = useState(false);
  const [copyLabel, setCopyLabel] = useState<string | null>(null);

  useEffect(() => {
    if (saveResult) setNote(saveResult.message);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saveResult]);

  const requireLoginItem = settings?.settings.require_login;
  const stored = requireLoginItem?.value ?? false;
  const chosen = want ?? stored;

  function choose(next: boolean) {
    if (busy) return;
    onChoose(next === stored ? null : next);
  }

  async function handleCopy() {
    if (!token) return;
    const ok = await copyText(token);
    setCopyLabel(ok ? t("common.btn.copied") : t("common.token.copyFailed"));
    setTimeout(() => setCopyLabel(null), ok ? 1400 : 2000);
  }

  return (
    <div className="set-field">
      <span className="set-label">{t("settings.general.requireLogin.label")}</span>
      <Segmented
        className="set-toggle"
        value={chosen ? "on" : "off"}
        disabled={busy}
        onChange={(v) => choose(v === "on")}
        options={[
          { label: t("settings.toggle.off"), value: "off" },
          { label: t("settings.toggle.on"), value: "on" },
        ]}
      />
      <p className="set-note">
        {t("settings.general.requireLogin.noteOff")}
        <br />
        {t("settings.general.requireLogin.noteOn")}
      </p>
      {requireLoginItem?.overridden && (
        <p className="set-override">{t("settings.overrideNote", { var: requireLoginItem.env_var })}</p>
      )}
      {chosen !== stored && (
        <p className="set-override">
          {t("settings.notSavedToggle", { state: t(stored ? "settings.state.on" : "settings.state.off") })}
        </p>
      )}
      <InlineNote text={note} tone={saveResult?.ok === false ? "error" : "success"} />

      {token && (
        <>
          <div className="set-divider" />
          <label className="fs-label" htmlFor="require-login-token">
            {t("settings.general.requireLogin.tokenLabel")}
          </label>
          <div className="tok-row">
            <Input id="require-login-token" className="tok-value" readOnly value={revealed ? token : maskToken(token)} />
            <Button
              title={revealed ? t("common.token.hideTitle") : t("common.token.showTitle")}
              aria-pressed={revealed}
              onClick={() => setRevealed((v) => !v)}
            >
              {revealed ? t("common.token.hide") : t("common.token.show")}
            </Button>
            <Button title={t("common.token.copyTokenTitle")} onClick={handleCopy}>
              {copyLabel ?? t("common.btn.copy")}
            </Button>
          </div>
          <p className="tok-note">{t("settings.general.requireLogin.tokenNote")}</p>
        </>
      )}
    </div>
  );
}
