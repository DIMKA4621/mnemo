"use client";

import type { ReactNode } from "react";
import { useT } from "@/lib/i18n/hooks";
import type { DoctorResult, DoctorReportStaleProject } from "@/lib/api/settings";
import { OrphanCleanup } from "./OrphanCleanup";

function Stat({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div className="set-stat">
      <span className="set-stat-label">{label}</span>
      <span className={`set-stat-value${mono ? " is-mono" : ""}`}>{value ?? "—"}</span>
    </div>
  );
}

export function MaintItem({ title, value, note, tone }: { title: string; value?: ReactNode; note?: ReactNode; tone?: "ok" | "warn" | "error" }) {
  return (
    <div className={`maint-item${tone ? ` is-${tone}` : ""}`}>
      <div className="maint-item-line">
        <strong>{title}</strong>
        {value != null && <code>{value}</code>}
      </div>
      {note != null && <p className="set-note">{note}</p>}
    </div>
  );
}

/**
 * The structured facts `GET /api/doctor` reports (`src/diagnostics.py`'s
 * `collect()`), grouped for scanning rather than poured into a preformatted
 * CLI transcript — ported near-verbatim from the vanilla console's
 * `renderMaintSection`. `disk`/`self_update` are optional on the type (the
 * dev fixture omits both) and simply not rendered when absent.
 */
export function DoctorReport({ report, onOrphansCleaned }: { report: DoctorResult; onOrphansCleaned: () => void }) {
  const t = useT();
  const provider = report.provider;
  const model = report.model;
  const vec = report.sqlite_vec;
  const resident = report.resident;
  const endpoint = report.endpoint;
  const backend = report.backend;
  const token = report.token;
  const registry = report.registry;
  const wiring = report.wiring;

  const providerValue = provider.machine + (provider.overrides.length ? ` · overrides: ${provider.overrides.join(", ")}` : "");
  const modelText = model.needed
    ? t(model.cached ? "settings.maint.model.cachedFull" : "settings.maint.model.notLoaded")
    : t(model.cached ? "settings.maint.model.cachedNotNeeded" : "settings.maint.model.notNeeded");
  const residentText = resident.applicable
    ? `${t(resident.up ? "settings.maint.resident.up" : "settings.maint.resident.down")} · ${resident.host}:${resident.port}${t("settings.maint.resident.portSuffix")}`
    : t("settings.maint.resident.na");
  const endpointText = endpoint.configured
    ? `${endpoint.url} · ${endpoint.model} · ${endpoint.dim} ${t("settings.maint.endpoint.dimsUnit")}${endpoint.key_set ? " · key set" : " · no key"}`
    : t("settings.maint.endpoint.notConfigured", { error: endpoint.error || t("settings.maint.unknown") });
  const backendText = backend.up
    ? t("settings.maint.backend.upSummary", { pid: backend.serving_pid ?? "—" })
    : t("settings.maint.backend.down", { error: backend.error || t("settings.maint.unknown") });
  const tokenText = token.present
    ? `set · ${token.where || token.source || "unknown"} · ${token.scope || "unknown scope"}`
    : t("settings.maint.token.notSet");

  return (
    <>
      <div className="set-field">
        <span className="set-label">{t("settings.maint.engineLabel")}</span>
        <div className="set-stats">
          <Stat label="Engine home" value={report.engine.home} mono />
          <Stat label="State dir" value={report.engine.state_dir} mono />
          <Stat label="Python" value={report.engine.python} mono />
        </div>
      </div>

      <div className="set-field">
        <span className="set-label">{t("settings.maint.embedLabel")}</span>
        <div className="set-stats">
          <Stat label={t("settings.maint.providerLabel")} value={providerValue} mono />
          <Stat label={t("settings.maint.localModelLabel")} value={modelText} />
          <Stat label="sqlite-vec" value={vec.ok ? "ok" : t("settings.maint.unavailable")} />
          <Stat label={t("settings.maint.residentLabel")} value={residentText} />
          {endpoint.applicable && <Stat label="API endpoint" value={endpointText} mono />}
        </div>
      </div>
      {vec.error && <p className="modal-error">{vec.error}</p>}

      <div className="set-field">
        <span className="set-label">{t("settings.maint.serviceLabel")}</span>
        <div className="set-stats">
          <Stat label="Backend" value={backendText} />
          <Stat label="URL" value={backend.url} mono />
          <Stat label={t("settings.maint.queueLabel")} value={backend.queue_depth} />
          <Stat label="API token" value={tokenText} mono />
        </div>
      </div>

      {!registry.ok ? (
        <div className="set-field">
          <span className="set-label">{t("settings.maint.registryLabel")}</span>
          <p className="modal-error">{t("settings.maint.registryUnreadable", { error: registry.error || t("settings.maint.unknown") })}</p>
        </div>
      ) : (
        <div className="set-field">
          <span className="set-label">{t("settings.maint.registryLabel")}</span>
          <div className="maint-list">
            <MaintItem title={t("memory.count.banks", { n: registry.count ?? 0 })} note={t("settings.maint.registryReadable")} tone="ok" />
            {registry.banks.map((bank) => {
              const flags: string[] = [];
              if (bank.state !== "enabled") flags.push(bank.state);
              if (!bank.exists) flags.push(t("settings.maint.registry.noRoot"));
              return (
                <MaintItem
                  key={bank.id}
                  title={bank.name}
                  value={flags.length ? flags.join(" · ") : "ok"}
                  note={bank.root}
                  tone={flags.length ? "warn" : undefined}
                />
              );
            })}
          </div>
        </div>
      )}

      <div className="set-field">
        <span className="set-label">Project wiring</span>
        <div className="maint-list">
          {!wiring.ok ? (
            <MaintItem title={t("settings.maint.unknownTitle")} note={wiring.error || t("settings.maint.genericError")} tone="error" />
          ) : !wiring.stale.length ? (
            <MaintItem title={t("settings.maint.count.projects", { n: wiring.total ?? 0 })} value={t("settings.maint.wiring.allCurrent")} tone="ok" />
          ) : (
            wiring.stale.map((project: DoctorReportStaleProject) => (
              <MaintItem key={project.root} title={project.root} value={project.command} note={project.reason} tone="warn" />
            ))
          )}
        </div>
      </div>

      <OrphanCleanup orphans={report.orphans} onCleaned={onOrphansCleaned} />
    </>
  );
}
