"use client";

import { useState } from "react";
import { Button, Checkbox } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { useFile } from "@/hooks/useMemoryQueries";
import { useReindex } from "@/hooks/useMemoryMutations";
import { fmtBytes } from "@/lib/memory/format";
import { ChunkBoundaryOverlay } from "./ChunkBoundaryOverlay";
import { useInlineNote, InlineNote } from "@/components/common/InlineNote";

interface FileViewerProps {
  bankId: string | null;
  path: string | null;
  /** Below 940px, whether this pane is the one currently shown by the
   *  mobile drill-down (`page.tsx`'s `mobPane`). */
  isMob?: boolean;
  /** Below 940px only: drills back to the Файли pane. */
  onBack?: () => void;
}

export function FileViewer({ bankId, path, isMob, onBack }: FileViewerProps) {
  const t = useT();
  const fileQuery = useFile(bankId, path);
  const reindexMutation = useReindex();
  // Session-only, deliberately not persisted (confirmed from the
  // pre-cutover implementation — the toggle always starts checked).
  const [showChunks, setShowChunks] = useState(true);
  const [note, setNote] = useInlineNote();

  const file = fileQuery.data;

  function handleReindexFile() {
    if (!bankId || !path) return;
    reindexMutation.mutate(
      { bank: bankId, path },
      {
        onSuccess: (res) =>
          setNote(
            t("common.reindex.queuedNote", {
              what: path,
              n: res.queued,
              ids: res.task_ids.join(", "),
            }),
          ),
        onError: (err) => setNote(err instanceof Error ? err.message : String(err)),
      },
    );
  }

  return (
    <div className={`pane${isMob ? " is-mob" : ""}`}>
      <div className="pane-head">
        <button type="button" className="pane-back" onClick={onBack}>
          {t("common.btn.back")}
        </button>
        <h2>{t("memory.pane.content")}</h2>
        <div className="pane-actions">
          <Checkbox
            checked={showChunks}
            onChange={(e) => setShowChunks(e.target.checked)}
            title={t("memory.pane.chunkVizTitle")}
          >
            {t("memory.pane.chunkVizLabel")}
          </Checkbox>
          <Button
            size="small"
            disabled={!file || reindexMutation.isPending}
            onClick={handleReindexFile}
          >
            {t("memory.pane.reindexFileBtn")}
          </Button>
        </div>
      </div>
      <div className="pane-body">
        {!path ? (
          <p className="empty-hint">{t("memory.pane.selectFileHint")}</p>
        ) : !file ? (
          <p className="empty-hint">{t("memory.tree.loading")}</p>
        ) : (
          <>
            <div className="file-meta">
              <div className="file-meta-path" title={file.path}>{file.path}</div>
              <div className="file-meta-info">
                <span>{fmtBytes(file.size)}</span>
                <span>{file.indexed ? t("memory.indexedState.yes") : t("memory.indexedState.no")}</span>
                <span>{t("memory.count.chunks", { n: file.chunks.length })}</span>
                <span title={file.sha256}>sha256 {file.sha256.slice(0, 12)}</span>
              </div>
            </div>
            <div className="doc">
              <ChunkBoundaryOverlay text={file.text} chunks={file.chunks} showChunks={showChunks} />
            </div>
            <div style={{ padding: "0 14px" }}>
              <InlineNote text={note} tone={reindexMutation.isError ? "error" : "success"} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
