"use client";

import { createPortal } from "react-dom";
import { Button, Segmented } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { usePageHeaderSlotStore } from "@/lib/store/page-header-slot";

interface JournalHeaderProps {
  kind: "query" | "index";
  onKindChange: (kind: "query" | "index") => void;
  onRefresh: () => void;
}

/** Portals into the shell's persistent `Topbar`, same mechanism as
 *  `MemoryPageHeader.tsx` — see that file's docstring for why a portal
 *  rather than a page-local bar. No mobile pane switcher here: Journal is a
 *  fixed desktop two-pane layout, no drill-down (scope cut, MN-35). */
export function JournalHeader({ kind, onKindChange, onRefresh }: JournalHeaderProps) {
  const t = useT();
  const slot = usePageHeaderSlotStore((s) => s.slot);

  if (!slot) return null;

  return createPortal(
    <>
      <span className="page-title">{t("shell.nav.journal")}</span>
      <Segmented
        size="small"
        value={kind}
        onChange={(v) => onKindChange(v as "query" | "index")}
        options={[
          { label: t("journal.header.segQuery"), value: "query" },
          { label: t("journal.header.segIndex"), value: "index" },
        ]}
      />
      <div style={{ flex: 1 }} />
      <Button
        size="small"
        title={t("journal.header.refreshTitle")}
        aria-label={t("journal.header.refreshTitle")}
        onClick={onRefresh}
      >
        ↻
      </Button>
    </>,
    slot,
  );
}
