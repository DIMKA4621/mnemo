"use client";

import { useEffect, useState } from "react";
import { MemoryPageHeader } from "@/components/memory/MemoryPageHeader";
import { RebuildPendingBanner } from "@/components/memory/RebuildPendingBanner";
import { BankList } from "@/components/memory/BankList";
import { BankPicker } from "@/components/memory/BankPicker";
import { BankTokenPanel } from "@/components/memory/BankTokenPanel";
import { RemovalDialog } from "@/components/memory/RemovalDialog";
import { FileTree } from "@/components/memory/FileTree";
import { FileViewer } from "@/components/memory/FileViewer";
import { PaneResizer } from "@/components/common/PaneResizer";
import { useT } from "@/lib/i18n/hooks";
import { useInlineNote, InlineNote } from "@/components/common/InlineNote";
import { usePaneWidthsStore } from "@/lib/store/pane-widths";
import type { BankInfo } from "@/lib/api/memory";
import "@/components/memory/memory.css";
import "@/components/common/dialogs.css";

/**
 * Below 940px exactly one pane is visible at a time, driven by selection
 * (drill-down) plus a back-arrow — never a persistent tab bar (tried and
 * dropped, lead decision on MN-34). Unused at ≥940px, where CSS shows all
 * three panes regardless of this value.
 */
type MobPane = "banks" | "tree" | "file";

export default function MemoryPage() {
  const t = useT();
  const widths = usePaneWidthsStore((s) => s.widths);
  const hydratePaneWidths = usePaneWidthsStore((s) => s.hydrate);
  const beginDrag = usePaneWidthsStore((s) => s.beginDrag);
  const applyDrag = usePaneWidthsStore((s) => s.applyDrag);
  const commitDrag = usePaneWidthsStore((s) => s.commitDrag);

  const [mobPane, setMobPane] = useState<MobPane>("banks");
  const [selectedBankId, setSelectedBankId] = useState<string | null>(null);
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [tokenBank, setTokenBank] = useState<BankInfo | null>(null);
  const [removalBank, setRemovalBank] = useState<BankInfo | null>(null);
  const [pickerNote, setPickerNote] = useInlineNote();

  useEffect(() => {
    hydratePaneWidths();
  }, [hydratePaneWidths]);

  function selectBank(bankId: string | null) {
    if (bankId === selectedBankId) return;
    setSelectedBankId(bankId);
    setSelectedFilePath(null);
    // Drill-down: picking a real bank advances the mobile pane to Файли.
    // Deselection (e.g. the selected bank got removed) doesn't — there's no
    // forward navigation intent to mirror there.
    if (bankId) setMobPane("tree");
  }

  function selectFile(path: string) {
    setSelectedFilePath(path);
    setMobPane("file");
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <MemoryPageHeader onAddBank={() => setPickerOpen(true)} />
      <RebuildPendingBanner />
      <div
        className="layout"
        style={{ gridTemplateColumns: `${widths[0]}px 6px ${widths[1]}px 6px minmax(0, 1fr)` }}
      >
        <div className={`pane${mobPane === "banks" ? " is-mob" : ""}`}>
          <div className="pane-head">
            <h2>{t("memory.pane.banks")}</h2>
          </div>
          <div className="pane-body">
            <BankList
              selectedBankId={selectedBankId}
              onSelect={selectBank}
              onOpenToken={setTokenBank}
              onOpenRemoval={setRemovalBank}
            />
          </div>
        </div>

        <PaneResizer onStart={beginDrag} onDrag={(dx) => applyDrag(0, dx)} onCommit={commitDrag} />

        <FileTree
          key={selectedBankId ?? "none"}
          bankId={selectedBankId}
          selectedPath={selectedFilePath}
          onSelectFile={selectFile}
          isMob={mobPane === "tree"}
          onBack={() => setMobPane("banks")}
        />

        <PaneResizer onStart={beginDrag} onDrag={(dx) => applyDrag(1, dx)} onCommit={commitDrag} />

        <FileViewer
          bankId={selectedBankId}
          path={selectedFilePath}
          isMob={mobPane === "file"}
          onBack={() => setMobPane("tree")}
        />
      </div>

      <BankPicker
        key={pickerOpen ? "picker-open" : "picker-closed"}
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onAdded={(bank, note) => {
          setPickerOpen(false);
          selectBank(bank.id);
          setPickerNote(note);
        }}
      />
      <BankTokenPanel key={`token-${tokenBank?.id ?? "none"}`} bank={tokenBank} onClose={() => setTokenBank(null)} />
      <RemovalDialog
        key={`removal-${removalBank?.id ?? "none"}`}
        bank={removalBank}
        onClose={() => setRemovalBank(null)}
        onRemoved={(bankId) => {
          setRemovalBank(null);
          if (selectedBankId === bankId) selectBank(null);
        }}
      />
      <InlineNote text={pickerNote} tone="success" />
    </div>
  );
}
