"use client";

import { useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import { useT } from "@/lib/i18n/hooks";

interface HitSnapshotProps {
  content: string;
}

/** The snapshot block: the first lines (clamped in `journal.css`), and the
 *  whole block toggles to the full chunk in place — jumping the reader to a
 *  fresh box elsewhere reads as navigating away from what they were reading. */
export function HitSnapshot({ content }: HitSnapshotProps) {
  const t = useT();
  const ref = useRef<HTMLSpanElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);
  const textId = useId();
  const text = content.trim();

  // Whether the clamp cut anything depends on the pane's width, so it is
  // measured, and re-measured on resize, only while collapsed.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || expanded) return;
    const measure = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [expanded, text]);

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

  const toggle = clamped || expanded;
  const className = `hit-snap${expanded ? "" : " is-clamped"}${toggle ? " is-toggle" : ""}`;

  if (!toggle) {
    return (
      <div className={className}>
        <span ref={ref} className="hit-snap-text">
          {text}
        </span>
      </div>
    );
  }

  return (
    <div
      className={className}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
      // A button's content is not read out, so the snippet rides as its description.
      aria-label={expanded ? t("journal.hit.collapseSnippet") : t("journal.hit.expandSnippet")}
      aria-describedby={textId}
      onClick={onClick}
      onKeyDown={onKeyDown}
    >
      <span ref={ref} id={textId} className="hit-snap-text">
        {text}
      </span>
    </div>
  );
}
