"use client";

import { useState } from "react";
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

/** The snapshot block: first two sentences, with a toggle that expands to
 *  the full chunk in the SAME block rather than a separate area — jumping
 *  the reader to a fresh box elsewhere reads as navigating away from what
 *  they were just reading (the mockup's original behavior, dropped). */
export function HitSnapshot({ content }: HitSnapshotProps) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const trimmed = content.trim();
  const sentences = splitSentences(trimmed);

  if (sentences.length <= PREVIEW_SENTENCES) {
    return <div className="hit-snap">{trimmed}</div>;
  }

  const preview = sentences.slice(0, PREVIEW_SENTENCES).join("").trim();

  return (
    <div className={`hit-snap${expanded ? " is-expanded" : ""}`}>
      <span>{expanded ? trimmed : preview}</span>{" "}
      <button type="button" className="hit-snap-more" onClick={() => setExpanded((v) => !v)}>
        {expanded ? t("journal.hit.collapse") : t("journal.hit.showMore")}
      </button>
    </div>
  );
}
