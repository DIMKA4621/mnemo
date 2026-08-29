"use client";

import { createPortal } from "react-dom";
import { useState } from "react";
import { Tabs, Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { usePageHeaderSlotStore } from "@/lib/store/page-header-slot";
import { GeneralSection } from "./GeneralSection";
import { EmbedSection } from "./EmbedSection";
import { MaintenanceSection } from "./MaintenanceSection";
import "./settings.css";

/** What a Save-gated section (Загальні, Модель ембедингу) hands the shared
 *  footer button — Обслуговування registers none, so the button is hidden
 *  entirely there, not disabled. */
export interface SectionController {
  hasPendingChange: boolean;
  busy: boolean;
  submit: () => void | Promise<void>;
}

type SectionId = "general" | "embed" | "maint";

/**
 * Horizontal tabs under the page header (AntD `Tabs`) — the current
 * console's replacement for the old fixed `.screen` overlay with a vertical
 * nav. Owns the one shared «Зберегти» button in the footer: each section
 * registers its own `{hasPendingChange, busy, submit}` via `onController`
 * while it is mounted (`GeneralSection`, `EmbedSection`) and clears it on its
 * own unmount cleanup — switching tabs unmounts the previous section (React
 * runs that cleanup before the newly-active section's own mount effect
 * registers its controller, in the same commit), which is what drops the
 * old controller and every pending edit/save verdict with it, no extra
 * effect needed here — same net result as the vanilla console's
 * `chooseSettingsSection` doing so by hand.
 */
export function SettingsTabs() {
  const t = useT();
  const slot = usePageHeaderSlotStore((s) => s.slot);
  const [active, setActive] = useState<SectionId>("general");
  const [controller, setController] = useState<SectionController | null>(null);

  return (
    <div className="set-page">
      {slot &&
        createPortal(
          <>
            <span className="page-title">{t("settings.header.title")}</span>
            <span className="page-sub">{t("settings.header.sub")}</span>
          </>,
          slot,
        )}

      <Tabs
        activeKey={active}
        onChange={(key) => setActive(key as SectionId)}
        items={[
          { key: "general", label: t("settings.tabs.general") },
          { key: "embed", label: t("settings.tabs.embed") },
          { key: "maint", label: t("settings.tabs.maint") },
        ]}
        style={{ padding: "0 12px" }}
      />

      <div className="set-body">
        <div className="set-form">
          <p className="lede">{t(`settings.lede.${active}`)}</p>
          {active === "general" && <GeneralSection onController={setController} />}
          {active === "embed" && <EmbedSection onController={setController} />}
          {active === "maint" && <MaintenanceSection />}
        </div>
      </div>

      {controller && (
        <div className="set-foot">
          <Button
            type="primary"
            disabled={!controller.hasPendingChange || controller.busy}
            loading={controller.busy}
            onClick={() => controller.submit()}
          >
            {t("settings.btn.save")}
          </Button>
        </div>
      )}
    </div>
  );
}
