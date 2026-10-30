"use client";

import { createPortal } from "react-dom";
import { Button, Segmented } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { usePageHeaderSlotStore } from "@/lib/store/page-header-slot";
import type { CatalogCategory } from "@/lib/api/catalog";

interface RegistryTabsProps {
  category: CatalogCategory;
  onCategoryChange: (category: CatalogCategory) => void;
  onAdd: () => void;
}

/**
 * Portals into the shell's persistent `Topbar`, same mechanism as
 * `MemoryPageHeader`/`JournalHeader` — page title, the MCP/Skills/Rules
 * category switcher, and "＋ Add" all live in that one row.
 */
export function RegistryTabs({ category, onCategoryChange, onAdd }: RegistryTabsProps) {
  const t = useT();
  const slot = usePageHeaderSlotStore((s) => s.slot);

  if (!slot) return null;

  return createPortal(
    <>
      <span className="page-title">{t("shell.nav.registry")}</span>
      <Segmented
        size="small"
        value={category}
        onChange={(v) => onCategoryChange(v as CatalogCategory)}
        options={[
          { label: t("registry.tabs.mcp"), value: "mcp" },
          { label: t("registry.tabs.skill"), value: "skill" },
          { label: t("registry.tabs.rule"), value: "rule" },
        ]}
      />
      <div style={{ flex: 1 }} />
      <Button size="small" title={t("registry.header.addTitle")} onClick={onAdd}>
        {t("registry.header.add")}
      </Button>
    </>,
    slot,
  );
}
