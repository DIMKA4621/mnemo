"use client";

import { useState } from "react";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useCleanOrphans } from "@/hooks/useSettingsMutations";
import { fmtBytes } from "@/lib/memory/format";
import type { DoctorReportOrphan, DoctorReportOrphans } from "@/lib/api/settings";
import { MaintItem } from "./DoctorReport";

function orphanNote(t: (key: string, vars?: Record<string, string | number>) => string, orphan: DoctorReportOrphan): string {
  if (orphan.error) return t("settings.maint.orphans.unreadable", { error: orphan.error });
  let note = orphan.root || (orphan.schema == null ? t("settings.maint.orphans.preV3NoRoot") : t("settings.maint.orphans.noRoot"));
  if (orphan.root_exists) note += t("settings.maint.orphans.rootStillOnDisk");
  return note;
}

/**
 * Index files no registered bank claims — deletion is a real, irreversible
 * loss (`POST /api/clean-orphans` only accepts ids this very list just
 * showed, so a stale list can never delete something newer than what the
 * user actually confirmed). Ported from the vanilla console's
 * `renderOrphanMaintenance`.
 */
export function OrphanCleanup({ orphans, onCleaned }: { orphans: DoctorReportOrphans; onCleaned: () => void }) {
  const t = useT();
  const cleanupMutation = useCleanOrphans();
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [lockedError, setLockedError] = useState<string | null>(null);

  const ids = (orphans.items ?? []).map((item) => item.id);

  async function submit() {
    if (!ids.length) return;
    setLockedError(null);
    try {
      const result = await cleanupMutation.mutateAsync(ids);
      const parts = [
        t("settings.maint.orphans.result.removed", { removed: result.removed.length, total: result.requested.length }),
        t("settings.maint.orphans.result.freed", { bytes: fmtBytes(result.freed_bytes) }),
      ];
      if (result.skipped.length) parts.push(t("settings.maint.orphans.result.skipped", { n: result.skipped.length }));
      if (result.locked.length) parts.push(t("settings.maint.orphans.result.locked", { n: result.locked.length }));
      setNote(parts.join(" · "));
      setConfirming(false);
      if (result.locked.length) {
        setLockedError(
          t("settings.maint.orphans.lockedError", {
            list: result.locked.map((item) => `${item.id} (${item.paths.join(", ")})`).join(" · "),
          }),
        );
      }
      onCleaned();
    } catch (err) {
      setLockedError(err instanceof Error ? err.message : String(err));
    }
  }

  const label =
    t("settings.maint.orphans.sectionLabel") + (orphans.ok && orphans.count ? ` · ${orphans.count} · ${fmtBytes(orphans.bytes ?? 0)}` : "");

  return (
    <div className="set-field">
      <span className="set-label">{label}</span>
      <div className="maint-list">
        {!orphans.ok ? (
          <MaintItem
            title={t("settings.maint.orphans.unavailableTitle")}
            note={t("settings.maint.orphans.deletionForbidden", { reason: orphans.error || t("settings.maint.orphans.registryUncheckable") })}
            tone="error"
          />
        ) : !orphans.count ? (
          <MaintItem title={t("settings.maint.orphans.noneTitle")} value="0 B" note={t("settings.maint.orphans.noneNote")} tone="ok" />
        ) : (
          (orphans.items ?? []).map((orphan) => (
            <MaintItem
              key={orphan.id}
              title={orphan.id}
              value={fmtBytes(orphan.size)}
              note={`${orphan.files == null ? t("settings.maint.orphans.unknownFiles") : t("memory.count.files", { n: orphan.files })} · ${orphanNote(t, orphan)}`}
              tone="warn"
            />
          ))
        )}
      </div>
      <p className="set-note">{t("settings.maint.orphans.sectionNote")}</p>
      {note && <p className="tok-ok">{note}</p>}
      {lockedError && <p className="modal-error">{lockedError}</p>}

      {orphans.ok && !!orphans.count && (
        confirming ? (
          <div className="tok-confirm">
            <p className="tok-confirm-text">{t("settings.maint.orphans.confirmText", { ids: ids.join(", ") })}</p>
            <div className="tok-confirm-row">
              <Button size="small" disabled={cleanupMutation.isPending} onClick={() => setConfirming(false)}>
                {t("common.btn.cancel")}
              </Button>
              <Button size="small" danger loading={cleanupMutation.isPending} onClick={submit}>
                {cleanupMutation.isPending ? t("settings.maint.orphans.cleaning") : t("settings.maint.orphans.deleteBtn", { n: ids.length })}
              </Button>
            </div>
          </div>
        ) : (
          <div className="maint-actions">
            <Button size="small" onClick={() => setConfirming(true)}>
              {t("settings.maint.orphans.cleanupBtn")}
            </Button>
          </div>
        )
      )}
    </div>
  );
}
