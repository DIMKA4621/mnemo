"use client";

import { useT } from "@/lib/i18n/hooks";
import { useUpdateStatus } from "@/hooks/useSettingsQueries";
import { useUpdateModalStore } from "@/lib/store/update-modal";
import "./settings.css";

/** Strips the local-build trailing `l` suffix (`v3.1.0l`) a just-switched-to
 *  dev build reports as `current.tag`, so it compares equal to the plain
 *  release tag `latest_known` still names — otherwise a machine that just
 *  updated to a local build would show the banner for the update it already
 *  has. */
function baseVersionTag(tag: string | null): string | null {
  if (!tag) return tag;
  return /\dl$/.test(tag) ? tag.slice(0, -1) : tag;
}

/**
 * Sidebar-footer banner — precedence: a busy apply beats an auto-pending
 * countdown beats a plain "update available" notice beats nothing at all.
 * Reads the same `GET /api/update/status` cache `UpdateModal` and
 * `AutoUpdateField`'s manual check populate; clicking it only ever opens
 * the modal (`UpdateModal`, mounted once at shell level) at the matching
 * phase — this component owns no dialog of its own.
 */
export function UpdateBanner() {
  const t = useT();
  const statusQuery = useUpdateStatus();
  const phase = useUpdateModalStore((s) => s.phase);
  const setPhase = useUpdateModalStore((s) => s.setPhase);
  const setEverSwitching = useUpdateModalStore((s) => s.setEverSwitching);

  const u = statusQuery.data;
  if (!u) return null;

  const apply = u.apply;
  const busy = apply.state === "staging" || apply.state === "switching";
  const pending = u.auto.pending;

  if (busy) {
    return (
      <button
        type="button"
        className="sb-update-banner is-busy"
        onClick={() => {
          setEverSwitching(apply.state === "switching");
          setPhase("progress");
        }}
      >
        {t("update.banner.busy")}
      </button>
    );
  }

  if (pending) {
    return (
      <button type="button" className="sb-update-banner" onClick={() => setPhase("auto-pending")}>
        {t("update.banner.autoPending", { tag: pending.tag })}
      </button>
    );
  }

  if (phase !== "idle") return null; // a modal phase already owns the moment

  const currentBase = baseVersionTag(u.current.tag);
  const latestTag = u.latest_known.tag;
  const alreadyOnLatest = !!currentBase && !!latestTag && currentBase === latestTag;
  if (!u.latest_known.update_available || alreadyOnLatest) return null;

  return (
    <button
      type="button"
      className="sb-update-banner"
      onClick={() => {
        setEverSwitching(false);
        setPhase("confirm");
      }}
    >
      {t("update.banner.available", { tag: latestTag ?? "" })}
    </button>
  );
}
