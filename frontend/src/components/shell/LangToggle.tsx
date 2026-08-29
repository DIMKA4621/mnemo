"use client";

import { useUiStore } from "@/lib/store/ui";

/** Same rationale as `ThemeToggle.tsx` for living in the shell now. */
export function LangToggle() {
  const lang = useUiStore((s) => s.lang);
  const setLang = useUiStore((s) => s.setLang);
  const next = lang === "en" ? "uk" : "en";

  return (
    <button
      type="button"
      className="sb-toggle"
      title={lang === "en" ? "Українською" : "In English"}
      aria-label={lang === "en" ? "Switch to Ukrainian" : "Switch to English"}
      onClick={() => setLang(next)}
    >
      <span style={{ fontSize: 11, fontFamily: "var(--mono)", fontWeight: 600 }}>
        {lang.toUpperCase()}
      </span>
    </button>
  );
}
