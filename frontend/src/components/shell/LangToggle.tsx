"use client";

import { Select } from "antd";
import { useUiStore } from "@/lib/store/ui";
import type { Lang } from "@/lib/store/ui";

const OPTIONS: { value: Lang; label: string }[] = [
  { value: "en", label: "EN" },
  { value: "uk", label: "UK" },
];

/**
 * A dropdown, not a toggle button (2026-08-29, user request): the previous
 * click-to-flip control didn't show which two languages exist, only
 * whatever it happened to switch to. `ThemeToggle` stays a plain icon
 * toggle — dark/light is inherently binary and the sun/moon icon already
 * shows the destination; language needed a visible option list instead.
 * Same rationale as `ThemeToggle.tsx` for living in the shell topbar now.
 */
export function LangToggle() {
  const lang = useUiStore((s) => s.lang);
  const setLang = useUiStore((s) => s.setLang);

  return (
    <Select<Lang>
      value={lang}
      onChange={setLang}
      options={OPTIONS}
      size="small"
      variant="borderless"
      popupMatchSelectWidth={false}
      style={{ width: 56 }}
      aria-label="Language"
    />
  );
}
