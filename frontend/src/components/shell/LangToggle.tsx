"use client";

import { useUiStore } from "@/lib/store/ui";
import type { Lang } from "@/lib/store/ui";

const LABELS: Record<Lang, string> = { en: "EN", uk: "UK" };
const OTHER: Record<Lang, Lang> = { en: "uk", uk: "en" };

/**
 * Back to a plain click-to-flip button (2026-08-29, reverted twice the same
 * day): first a `Select` (reserved its own wide box, broke the one-row
 * layout), then a `Dropdown` overlay (AntD's selected-menu-item styling
 * rendered as a near-black, barely-legible square around "EN" — visibly
 * broken, not a styling nit). With only two languages, a toggle showing the
 * *current* code — styled identically to `ThemeToggle`, same `.sb-toggle`
 * box, same click-to-flip interaction — carries none of that failure mode.
 */
export function LangToggle() {
  const lang = useUiStore((s) => s.lang);
  const setLang = useUiStore((s) => s.setLang);
  const next = OTHER[lang];

  return (
    <button
      type="button"
      className="sb-toggle lang-toggle"
      title={`Switch to ${LABELS[next]}`}
      aria-label={`Language: ${LABELS[lang]}. Switch to ${LABELS[next]}`}
      onClick={() => setLang(next)}
    >
      {LABELS[lang]}
    </button>
  );
}
