"use client";

import { Segmented } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useUiStore } from "@/lib/store/ui";

interface ChunksFieldProps {
  want: boolean | null;
  onChoose: (want: boolean | null) => void;
}

/** Памʼять's chunk-boundary overlay. Stored in this browser, not on the
 *  service, but picked and saved like the other General toggles. */
export function ChunksField({ want, onChoose }: ChunksFieldProps) {
  const t = useT();
  const stored = useUiStore((s) => s.showChunks);
  const chosen = want ?? stored;

  return (
    <div className="set-field">
      <span className="set-label">{t("settings.general.chunks.label")}</span>
      <Segmented
        className="set-toggle"
        value={chosen ? "on" : "off"}
        onChange={(v) => onChoose(v === "on" === stored ? null : v === "on")}
        options={[
          { label: t("settings.toggle.on"), value: "on" },
          { label: t("settings.toggle.off"), value: "off" },
        ]}
      />
      <p className="set-note">{t("settings.general.chunks.note")}</p>
      {chosen !== stored && (
        <p className="set-override">
          {t("settings.notSavedToggle", { state: t(stored ? "settings.state.on" : "settings.state.off") })}
        </p>
      )}
    </div>
  );
}
