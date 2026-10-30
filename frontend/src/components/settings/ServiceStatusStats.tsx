"use client";

import type { ReactNode } from "react";
import { useT } from "@/lib/i18n/hooks";
import type { StatusResult } from "@/lib/api/memory";

/** `2 год 14 хв` / `3 хв 05 с` / `47 с` — ported from the vanilla console's
 *  `humanUptime()`. Exported for `EmbedMemoryCard`'s `idle_timeout_s`, the
 *  only other place this console formats a duration this way. */
export function humanUptime(t: (key: string, vars?: Record<string, string | number>) => string, seconds: number): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return t("settings.uptime.hoursMinutes", { h, m });
  if (m) return t("settings.uptime.minutesSeconds", { m, s: String(s).padStart(2, "0") });
  return t("settings.uptime.seconds", { s });
}

function Stat({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="set-stat">
      <span className="set-stat-label">{label}</span>
      <span className={`set-stat-value${mono ? " is-mono" : ""}`}>{value == null ? "—" : value}</span>
    </div>
  );
}

/** Reports only. Stopping/restarting the backend are deliberately absent
 *  from this console entirely — it is served BY the process a stop button
 *  would kill, leaving no way back except a terminal — so the honest thing
 *  is to show the state, not offer a control that defeats itself. */
export function ServiceStatusStats({ status }: { status: StatusResult }) {
  const t = useT();
  const svc = status.service;

  return (
    <div className="set-field">
      <span className="set-label">{t("settings.general.statusLabel")}</span>
      <div className="set-stats">
        <Stat label={t("settings.general.stat.version")} value={svc.version} mono />
        <Stat label={t("settings.general.stat.pid")} value={svc.pid} mono />
        <Stat label={t("settings.general.stat.address")} value={`${svc.host || "—"}:${svc.port ?? "—"}`} mono />
        <Stat label={t("settings.general.stat.provider")} value={svc.provider} mono />
        <Stat label={t("settings.general.stat.uptime")} value={humanUptime(t, svc.uptime_s)} />
        <Stat
          label={t("settings.general.stat.priorityQueue")}
          value={t(svc.priority_enabled ? "settings.general.stat.priorityOn" : "settings.general.stat.priorityOff")}
        />
      </div>
    </div>
  );
}
