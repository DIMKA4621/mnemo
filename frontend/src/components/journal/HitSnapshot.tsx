"use client";

import { useId, useState, type KeyboardEvent, type MouseEvent } from "react";
import { useT } from "@/lib/i18n/hooks";

const PREVIEW_SENTENCES = 2;

/** Splits on sentence-ending punctuation followed by whitespace. Good
 *  enough for a preview cutoff, not a parser — text that never hits
 *  `. `/`? `/`! ` (a bullet list, a code block) comes back as one
 *  "sentence", which correctly skips the preview below. Ported from the
 *  vanilla console's `page-journal.js` (`splitSentences`). */
function splitSentences(text: string): string[] {
  return text.match(/[^.!?…]+[.!?…]+(\s+|$)|[^.!?…]+$/g) || [text];
}

interface HitSnapshotProps {
  content: string;
}

/** The snapshot block: first two sentences and a trailing ellipsis; the whole
 *  block toggles to the full chunk in place — jumping the reader to a fresh
 *  box elsewhere reads as navigating away from what they were just reading. */
export function HitSnapshot({ content }: HitSnapshotProps) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const textId = useId();
  const trimmed = content.trim();
  const sentences = splitSentences(trimmed);

  if (sentences.length <= PREVIEW_SENTENCES) {
    return <div className="hit-snap">{trimmed}</div>;
  }

  const preview = sentences.slice(0, PREVIEW_SENTENCES).join("").trim();

  // A click that ends a drag-selection inside the block is the user copying
  // text, not asking to toggle.
  function onClick(e: MouseEvent<HTMLDivElement>) {
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && e.currentTarget.contains(sel.anchorNode)) return;
    setExpanded((v) => !v);
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    setExpanded((v) => !v);
  }

  return (
    <div
      className={`hit-snap is-toggle${expanded ? " is-expanded" : ""}`}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      // A button's content is not read out, so the snippet rides as its description.
      aria-label={expanded ? t("journal.hit.collapseSnippet") : t("journal.hit.expandSnippet")}
      aria-describedby={textId}
      onClick={onClick}
      onKeyDown={onKeyDown}
    >
      <span id={textId}>{expanded ? trimmed : preview}</span>
      {!expanded && <span className="hit-snap-ellipsis" aria-hidden>…</span>}
    </div>
  );
}
