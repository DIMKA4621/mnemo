"use client";

import { useT } from "@/lib/i18n/hooks";
import { useCatalog } from "@/hooks/useCatalogQueries";
import { catalogEntryMeta } from "@/lib/registry/format";
import { StatusBadge } from "@/components/common/StatusBadge";
import type { CatalogCategory, CatalogEntry } from "@/lib/api/catalog";

interface RegistryListProps {
  category: CatalogCategory;
  selectedId: string | null;
  onSelect: (entry: CatalogEntry) => void;
}

/**
 * The category's entries, master-detail's left pane. No checkboxes, no
 * agent picker here — attaching an entry to a specific agent is that
 * agent's own ⚙ screen's concern (Фаза C, blocked by MN-48), not this page's.
 */
export function RegistryList({ category, selectedId, onSelect }: RegistryListProps) {
  const t = useT();
  const query = useCatalog(category);
  const entries = query.data ?? [];

  if (query.isLoading) {
    return (
      <div className="reg-list-pane">
        <p className="empty-hint">{t("registry.list.loading")}</p>
      </div>
    );
  }

  if (!entries.length) {
    return (
      <div className="reg-list-pane">
        <p className="empty-hint">{t("registry.list.empty")}</p>
      </div>
    );
  }

  return (
    <div className="reg-list-pane">
      {entries.map((entry) => (
        <button
          key={entry.id}
          type="button"
          className={`reg-row${entry.id === selectedId ? " is-selected" : ""}`}
          onClick={() => onSelect(entry)}
        >
          <div className="reg-row-text">
            <div className="reg-row-name">{entry.name}</div>
            <div className="reg-row-meta">{catalogEntryMeta(entry)}</div>
          </div>
          <StatusBadge variant="empty" text={t("registry.count.agents", { n: entry.used_by_count })} />
        </button>
      ))}
    </div>
  );
}
