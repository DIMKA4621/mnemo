"use client";

import { useRef } from "react";
import { useT } from "@/lib/i18n/hooks";

interface PaneResizerProps {
  onStart?: () => void;
  onDrag: (deltaX: number) => void;
  onCommit: () => void;
}

/**
 * One draggable 6px divider between two panes. Ported from the vanilla
 * console's shared `wireColumnResizer()` (`src/webui/static/app.js`):
 * listens on `document`, not the handle itself, so a fast mouse movement
 * that slips off the narrow track mid-drag doesn't drop the resize.
 * `body.classList` toggling (`is-resizing-pane`, for cursor/highlight during
 * the whole gesture) lives here since it's the one DOM side-effect specific
 * to dragging, not to what is being resized — clamping and applying the
 * width is the caller's job (`onDrag`/`onCommit`).
 */
export function PaneResizer({ onStart, onDrag, onCommit }: PaneResizerProps) {
  const t = useT();
  const startX = useRef(0);

  function handleMouseDown(ev: React.MouseEvent) {
    ev.preventDefault();
    startX.current = ev.clientX;
    onStart?.();
    document.body.classList.add("is-resizing-pane");

    const onMove = (moveEv: MouseEvent) => onDrag(moveEv.clientX - startX.current);
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.classList.remove("is-resizing-pane");
      onCommit();
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }

  return (
    <div
      className="mnemo-pane-resizer"
      title={t("common.resizerTitle")}
      onMouseDown={handleMouseDown}
    />
  );
}
