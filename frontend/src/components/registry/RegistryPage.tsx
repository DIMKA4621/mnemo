"use client";

import { useEffect, useState } from "react";
import { RegistryTabs } from "@/components/registry/RegistryTabs";
import { RegistryList } from "@/components/registry/RegistryList";
import { RegistryDetail } from "@/components/registry/RegistryDetail";
import { AddEntryModal } from "@/components/registry/AddEntryModal";
import { PaneResizer } from "@/components/common/PaneResizer";
import { useInlineNote, InlineNote } from "@/components/common/InlineNote";
import { useT } from "@/lib/i18n/hooks";
import { useRegistryPaneWidthStore } from "@/lib/store/registry-pane-width";
import type { CatalogCategory, CatalogEntry } from "@/lib/api/catalog";
import "@/components/registry/registry.css";
import "@/components/common/dialogs.css";

/**
 * Master-detail like Памʼять's banks→content: a category switch (in
 * `RegistryTabs`, portalled into the shell topbar) picks which list shows on
 * the left, a click on a row opens its full record on the right. Ported
 * layout from the live-verified mockup's `renderRegistry`
 * (`.claude/scratch/agents-page-mockup/app.js`) — no checkboxes, no agent
 * picker: binding a registry entry to an agent is that agent's own ⚙
 * screen's concern (Фаза C, blocked by MN-48), not this page's.
 */
export default function RegistryPage() {
  const t = useT();
  const width = useRegistryPaneWidthStore((s) => s.width);
  const hydrateWidth = useRegistryPaneWidthStore((s) => s.hydrate);
  const beginDrag = useRegistryPaneWidthStore((s) => s.beginDrag);
  const applyDrag = useRegistryPaneWidthStore((s) => s.applyDrag);
  const commitDrag = useRegistryPaneWidthStore((s) => s.commitDrag);

  const [category, setCategory] = useState<CatalogCategory>("mcp");
  const [selectedEntry, setSelectedEntry] = useState<CatalogEntry | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [note, setNote] = useInlineNote();

  useEffect(() => {
    hydrateWidth();
  }, [hydrateWidth]);

  function changeCategory(next: CatalogCategory) {
    if (next === category) return;
    setCategory(next);
    setSelectedEntry(null);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <RegistryTabs category={category} onCategoryChange={changeCategory} onAdd={() => setAddOpen(true)} />
      <div className="reg-list" style={{ gridTemplateColumns: `${width}px 6px minmax(0, 1fr)` }}>
        <RegistryList category={category} selectedId={selectedEntry?.id ?? null} onSelect={setSelectedEntry} />
        <PaneResizer onStart={beginDrag} onDrag={applyDrag} onCommit={commitDrag} />
        <RegistryDetail
          key={selectedEntry?.id ?? "none"}
          category={category}
          entry={selectedEntry}
          onSaved={(entry) => {
            setSelectedEntry(entry);
            setNote(t("registry.detail.updatedNote", { name: entry.name }));
          }}
        />
      </div>
      <AddEntryModal
        key={addOpen ? "add-open" : "add-closed"}
        open={addOpen}
        category={category}
        onClose={() => setAddOpen(false)}
        onAdded={(entry) => {
          setAddOpen(false);
          setSelectedEntry(entry);
          setNote(t("registry.addModal.addedNote", { name: entry.name }));
        }}
      />
      <InlineNote text={note} tone="success" />
    </div>
  );
}
