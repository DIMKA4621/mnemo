"use client";

import { useEffect } from "react";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useDoctor } from "@/hooks/useSettingsQueries";
import { DoctorReport } from "./DoctorReport";

/**
 * Diagnostics and cleanup — the rare commands, kept off the main screen.
 * `GET /api/doctor` is fetched once when this tab opens (not automatically
 * elsewhere, not polled — `useDoctor()`'s `enabled: false`) and again only
 * on an explicit «Оновити» click or after a successful orphan cleanup. No
 * «Зберегти» here: nothing on this tab is a stored setting, so
 * `SettingsTabs` never registers a controller for it.
 */
export function MaintenanceSection() {
  const t = useT();
  const doctorQuery = useDoctor();

  useEffect(() => {
    doctorQuery.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <>
      <div className="maint-head">
        <Button size="small" loading={doctorQuery.isFetching} disabled={doctorQuery.isFetching} onClick={() => doctorQuery.refetch()}>
          {doctorQuery.isFetching ? t("settings.maint.refreshing") : t("settings.maint.refreshBtn")}
        </Button>
      </div>

      {doctorQuery.error && (
        <p className="modal-error">{doctorQuery.error instanceof Error ? doctorQuery.error.message : String(doctorQuery.error)}</p>
      )}

      {!doctorQuery.data ? (
        <p className="empty-hint">{doctorQuery.isFetching ? t("settings.maint.collecting") : t("settings.maint.notFetched")}</p>
      ) : (
        <DoctorReport report={doctorQuery.data} onOrphansCleaned={() => doctorQuery.refetch()} />
      )}
    </>
  );
}
