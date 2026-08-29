"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type InlineNoteTone = "info" | "success" | "error";

const TONE_COLOR: Record<InlineNoteTone, string> = {
  info: "var(--fg-mute)",
  success: "var(--ok)",
  error: "var(--err)",
};

/**
 * Transient note tied to one control's own outcome — deliberately not a
 * global toast (ported behaviour from the vanilla console's `setNote()`,
 * `src/webui/static/app.js`): the message renders right next to the action
 * that produced it and clears itself after `ms`, rather than floating
 * somewhere else on the page.
 */
export function useInlineNote(ms = 6000): [string | null, (text: string | null) => void] {
  const [text, setText] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const set = useCallback(
    (next: string | null) => {
      if (timer.current) clearTimeout(timer.current);
      setText(next);
      if (next) {
        timer.current = setTimeout(() => setText(null), ms);
      }
    },
    [ms],
  );

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return [text, set];
}

export function InlineNote({ text, tone = "info" }: { text: string | null; tone?: InlineNoteTone }) {
  if (!text) return null;
  return (
    <p style={{ margin: "6px 0 0", fontSize: 12, color: TONE_COLOR[tone] }}>{text}</p>
  );
}
