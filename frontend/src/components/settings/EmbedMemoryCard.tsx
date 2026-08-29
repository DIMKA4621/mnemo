"use client";

import { useEffect, useState } from "react";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { StatusBadge } from "@/components/common/StatusBadge";
import { useEmbedState } from "@/hooks/useSettingsQueries";
import { useDownloadEmbedModel, useLoadEmbed, useUnloadEmbed } from "@/hooks/useSettingsMutations";
import { ApiError } from "@/lib/api/fetcher";
import { humanUptime } from "./ServiceStatusStats";

/**
 * What the active embedding backend is holding in memory, right now.
 * «Вивантажити» is NOT an off switch — the copy has to keep saying so: the
 * model comes back on the next search or indexed file, paying ~7-8s once.
 * A backend that is off is not a mode, it is a fault; this offers the
 * memory back on purpose, the trade `MNEMO_EMBED_IDLE_TIMEOUT=0` left to a
 * command instead of an idle timer.
 *
 * Deliberately NOT gated behind the shared «Зберегти»: unloading is not a
 * setting, it is an action with an immediate effect and no stored form, the
 * same category as a bank's reindex.
 */
export function EmbedMemoryCard() {
  const t = useT();
  const embedQuery = useEmbedState(true);
  const unloadMutation = useUnloadEmbed();
  const loadMutation = useLoadEmbed();
  const downloadMutation = useDownloadEmbedModel();
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!note) return;
    const timer = setTimeout(() => setNote(null), 5000);
    return () => clearTimeout(timer);
  }, [note]);

  const info = embedQuery.data ?? null;
  const busy = unloadMutation.isPending || loadMutation.isPending || downloadMutation.isPending;

  async function runUnload() {
    setError(null);
    setNote(null);
    try {
      await unloadMutation.mutateAsync();
      setNote(t("settings.embed.mem.unloadedNote"));
    } catch (err) {
      setError(err instanceof ApiError && err.code === "embed_busy" ? t("settings.embed.mem.busyError") : err instanceof Error ? err.message : String(err));
    }
  }

  async function runLoad() {
    setError(null);
    setNote(null);
    try {
      const result = await loadMutation.mutateAsync();
      setNote(
        result.holding === "n/a"
          ? t("settings.embed.mem.probeOkBase") +
              (result.probe_dim ? t("settings.embed.mem.probeOkDimSuffix", { dim: result.probe_dim }) : ".")
          : t("settings.embed.mem.loadedNote"),
      );
    } catch (err) {
      setError(err instanceof ApiError && err.code === "embed_busy" ? t("settings.embed.mem.busyError") : err instanceof Error ? err.message : String(err));
    }
  }

  async function runDownload() {
    setError(null);
    setNote(null);
    try {
      await downloadMutation.mutateAsync();
    } catch (err) {
      // `already_cached`/`download_in_progress` both mean a poll will pick
      // up the real state on its own (`useEmbedState`'s `refetchInterval`
      // reads `download.active` off whatever the next fetch reports) —
      // only an unrelated failure is worth showing.
      if (err instanceof ApiError && (err.code === "already_cached" || err.code === "download_in_progress")) return;
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!info) {
    return (
      <div className="set-field">
        <span className="set-label">{t("settings.embed.mem.label")}</span>
        <p className={embedQuery.error ? "modal-error" : "empty-hint"}>
          {embedQuery.error instanceof Error ? embedQuery.error.message : t("settings.embed.mem.notFetched")}
        </p>
      </div>
    );
  }

  const held = info.holding;
  const showProbe = held === "loaded" || held === "n/a" || held === "unknown" || (held === "unloaded" && info.cached !== false);
  const download = info.cached === false ? info.download : null;
  const holdLabel = t(`settings.embed.mem.hold.${held === "n/a" ? "na" : held}`);

  return (
    <div className="set-field">
      <span className="set-label">{t("settings.embed.mem.label")}</span>

      {(held === "loaded" || held === "unloaded") && (
        <p className="set-note mem-intro">
          <strong>{t("settings.embed.mem.introStrong")}</strong>
          {t("settings.embed.mem.introRest")}
        </p>
      )}

      <div className="set-mem-line">
        <span className="set-mem-caption">{t("settings.embed.mem.statusCaption")}</span>
        <StatusBadge variant={held === "loaded" ? "ready" : "empty"} text={holdLabel || String(held)} />
      </div>
      <div className="set-mem-line">
        <span className="set-mem-caption">{t("settings.embed.mem.modelCaption")}</span>
        <span className="set-mem-what">{info.model || "—"}</span>
      </div>
      {info.idle_timeout_s != null && (
        <div className="set-mem-line">
          <span className="set-mem-caption">{t("settings.embed.mem.idleCaption")}</span>
          <span className="set-mem-what">
            {info.idle_timeout_s > 0 ? humanUptime(t, info.idle_timeout_s) : t("settings.state.off")}
          </span>
        </div>
      )}

      {(held === "loaded" || showProbe) && (
        <div className="set-mem-actions">
          {held === "loaded" && (
            <Button size="small" disabled={busy} onClick={runUnload}>
              {t("settings.embed.mem.unloadBtn")}
            </Button>
          )}
          {showProbe && (
            <Button size="small" disabled={busy} onClick={runLoad}>
              {held === "unloaded"
                ? t("settings.embed.mem.wakeBtn")
                : held === "n/a" || held === "unknown"
                  ? t("settings.embed.mem.probeEndpointBtn")
                  : t("settings.embed.mem.probeBtn")}
            </Button>
          )}
        </div>
      )}

      {download && download.active && (
        <div className="set-mem-line">
          <i className="dot busy" />
          <span>{t("settings.embed.mem.downloading")}</span>
        </div>
      )}
      {download && !download.active && (
        <div className="set-mem-actions">
          <Button size="small" disabled={busy} onClick={runDownload}>
            {t("settings.embed.mem.downloadBtn")}
          </Button>
        </div>
      )}

      {error ? <p className="modal-error">{error}</p> : note ? <p className="tok-ok">{note}</p> : null}

      {held === "loaded" && info.wake_s ? <p className="set-note">{t("settings.embed.mem.note.wakeSoon")}</p> : null}
      {held === "n/a" && (
        <>
          <p className="set-note">{t("settings.embed.mem.note.naHosted")}</p>
          <p className="set-note">{t("settings.embed.mem.note.naProbeCost")}</p>
        </>
      )}
      {info.expires_at && <p className="set-note">{t("settings.embed.mem.note.expiresAt", { when: info.expires_at })}</p>}
      {info.others_held ? <p className="set-note">{t("settings.embed.mem.note.othersHeld", { n: info.others_held })}</p> : null}
      {download && !download.active && download.failed && <p className="set-note">{t("settings.embed.mem.note.downloadFailed")}</p>}
      {info.detail && <p className="set-note">{info.detail}</p>}
    </div>
  );
}
