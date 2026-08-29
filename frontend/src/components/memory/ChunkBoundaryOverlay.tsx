"use client";

import { useMemo } from "react";
import { useT } from "@/lib/i18n/hooks";
import { makeSlicer } from "@/lib/memory/chunkSlicer";
import type { ChunkInfo } from "@/lib/api/memory";

interface ChunkBoundaryOverlayProps {
  text: string;
  chunks: ChunkInfo[];
  showChunks: boolean;
}

function Gap({ text }: { text: string }) {
  const t = useT();
  if (!text) return null;
  const blank = text.trim() === "";
  return (
    <>
      {!blank && <div className="gap-note">{t("memory.chunk.gap")}</div>}
      <pre className={blank ? "gap-body is-blank" : "gap-body"}>{text}</pre>
    </>
  );
}

function Divider({ chunk }: { chunk: ChunkInfo }) {
  // Displayed 1-based; `chunk_index` stays 0-based everywhere else
  // (`chunk_uid` is derived from it — shifting the display value would
  // rewrite every chunk id and force a full re-embed of every bank).
  const label = `#${chunk.chunk_index + 1}${chunk.heading ? ` · ${chunk.heading}` : ""}`;
  return (
    <div className="chunk-divider" title={`chunk_uid ${chunk.chunk_uid}`}>
      <span className="cd-label">{label}</span>
      <span className="cd-range">{chunk.start_char}–{chunk.end_char}</span>
    </div>
  );
}

/**
 * Renders the file body with chunk boundaries drawn as divider lines over
 * the text — ported algorithm from the pre-cutover vanilla console
 * (`git show a5361db~1:src/webui/static/page-memory.js`, `renderFile()`).
 * `start_char`/`end_char` are Python code-point offsets; `makeSlicer` maps
 * them onto JS UTF-16 indices so an astral character (an emoji) doesn't
 * drift every boundary after it.
 */
export function ChunkBoundaryOverlay({ text, chunks, showChunks }: ChunkBoundaryOverlayProps) {
  const t = useT();
  const sorted = useMemo(
    () => chunks.slice().sort((a, b) => a.start_char - b.start_char),
    [chunks],
  );

  if (!showChunks || sorted.length === 0) {
    return <pre>{text}</pre>;
  }

  const slicer = makeSlicer(text);
  let cursor = 0;
  const nodes: React.ReactNode[] = [];

  for (const chunk of sorted) {
    const gap = slicer.slice(cursor, chunk.start_char);
    if (gap) nodes.push(<Gap key={`gap-${chunk.chunk_uid}`} text={gap} />);
    nodes.push(<Divider key={`div-${chunk.chunk_uid}`} chunk={chunk} />);
    nodes.push(
      <pre key={`body-${chunk.chunk_uid}`} className="chunk-body">
        {slicer.slice(chunk.start_char, chunk.end_char)}
      </pre>,
    );
    cursor = Math.max(cursor, chunk.end_char);
  }

  const trailing = slicer.slice(cursor, slicer.total);
  if (trailing) nodes.push(<Gap key="gap-trailing" text={trailing} />);

  nodes.push(
    <div className="chunk-divider is-end" key="end">
      <span className="cd-label">{t("memory.chunk.end", { n: slicer.total })}</span>
    </div>,
  );

  return <>{nodes}</>;
}
