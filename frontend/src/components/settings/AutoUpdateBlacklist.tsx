"use client";

import { useT } from "@/lib/i18n/hooks";
import { fmtDateTime } from "@/lib/memory/format";
import type { UpdateAutoStatus } from "@/lib/api/settings";
import { MaintItem } from "./DoctorReport";

/**
 * Tags mnemo's own unattended auto-apply tried and failed on repeatedly —
 * `GET /api/update/status`'s `auto.blacklist` (`engine_update.
 * record_auto_outcome`): permanently gave up (`blacklisted`), or still
 * inside its retry window. Read-only — no backend endpoint clears an entry,
 * so this renders and nothing more. Manual updates are unaffected either
 * way; this is diagnostic only.
 *
 * Hidden entirely when empty, unlike `OrphanCleanup`'s reassuring "none"
 * row: that section is a routine health check worth confirming on every
 * visit, this one is a rare failure trail almost nobody ever hits, so it
 * should cost the Maintenance tab zero space until it actually has
 * something to say.
 */
export function AutoUpdateBlacklist({ blacklist }: { blacklist: UpdateAutoStatus["blacklist"] }) {
  const t = useT();
  if (!blacklist.length) return null;

  return (
    <div className="set-field">
      <span className="set-label">
        {t("settings.maint.autoBlacklist.sectionLabel")} · {blacklist.length}
      </span>
      <div className="maint-list">
        {blacklist.map((entry) => {
          const parts = [t("settings.maint.count.attempts", { n: entry.attempts })];
          if (entry.last_failed_at) {
            parts.push(t("settings.maint.autoBlacklist.lastFailed", { date: fmtDateTime(entry.last_failed_at) }));
          }
          if (entry.blacklisted) {
            parts.push(t("settings.maint.autoBlacklist.permanentNote"));
          } else if (entry.next_retry_at) {
            parts.push(t("settings.maint.autoBlacklist.nextRetry", { date: fmtDateTime(entry.next_retry_at) }));
          }
          parts.push(entry.last_error || t("settings.maint.autoBlacklist.noError"));

          return (
            <MaintItem
              key={entry.tag}
              title={entry.tag}
              value={t(entry.blacklisted ? "settings.maint.autoBlacklist.blacklistedTitle" : "settings.maint.autoBlacklist.retryingTitle")}
              note={parts.join(" · ")}
              tone={entry.blacklisted ? "error" : "warn"}
            />
          );
        })}
      </div>
      <p className="set-note">{t("settings.maint.autoBlacklist.sectionNote")}</p>
    </div>
  );
}
