"use client";

import { createPortal } from "react-dom";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useBanks } from "@/hooks/useMemoryQueries";
import { usePageHeaderSlotStore } from "@/lib/store/page-header-slot";

interface MemoryPageHeaderProps {
  onAddBank: () => void;
}

/**
 * Composes into the shell's persistent `Topbar` via a portal targeting the
 * DOM node `usePageHeaderSlotStore` holds (`components/shell/Topbar.tsx`
 * publishes it through a `ref` callback) — the topbar itself is mounted
 * once in `AppShell` and never remounts on navigation, so this is the only
 * way a page can put its own title/actions into that same row instead of
 * opening a second, page-local bar underneath it.
 *
 * Below 940px there is no pane switcher here any more — navigation between
 * Банки/Файли/Вміст is pure drill-down driven by selection (`page.tsx`'s
 * `selectBank`/`selectFile`), with a back-arrow at the top of the Files and
 * Content panes (`FileTree.tsx`, `FileViewer.tsx`) undoing one step. A
 * persistent tab bar here was tried and dropped (lead decision, MN-34).
 */
export function MemoryPageHeader({ onAddBank }: MemoryPageHeaderProps) {
  const t = useT();
  const banksQuery = useBanks();
  const slot = usePageHeaderSlotStore((s) => s.slot);

  const banks = banksQuery.data ?? [];
  const files = banks.reduce((sum, b) => sum + (b.files || 0), 0);
  const chunks = banks.reduce((sum, b) => sum + (b.chunks || 0), 0);

  if (!slot) return null;

  return createPortal(
    <>
      <span className="page-title">{t("shell.nav.memory")}</span>
      <span className="page-sub">
        {t("memory.count.banks", { n: banks.length })} · {t("memory.count.files", { n: files })} ·{" "}
        {t("memory.count.chunks", { n: chunks })}
      </span>
      <div style={{ flex: 1 }} />
      <Button size="small" title={t("memory.header.addBankTitle")} onClick={onAddBank}>
        {t("memory.header.addBank")}
      </Button>
    </>,
    slot,
  );
}
