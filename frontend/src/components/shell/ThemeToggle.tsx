"use client";

import { useUiStore } from "@/lib/store/ui";

/**
 * Moved back into the shell from Settings → General (where it lived after
 * `.claude/memory/logs/2026-08-20-cabinet-shell.md`'s redesign) — that move
 * was to keep the control's state stable across the vanilla console's full
 * in-DOM re-render on every page switch. `app/layout.tsx` now persists
 * across route navigation, so the original reason no longer applies; see
 * the Phase 1 plan's shell-components list. Flagged for `docs-keeper` to
 * reconcile in `topics/console-ui.md` once this lands.
 */
export function ThemeToggle() {
  const theme = useUiStore((s) => s.theme);
  const setTheme = useUiStore((s) => s.setTheme);
  const next = theme === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      className="sb-toggle"
      title={theme === "dark" ? "Light theme" : "Dark theme"}
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
      onClick={() => setTheme(next)}
    >
      {theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

function SunIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 3v2.2M12 18.8V21M4.2 12H2.4M21.6 12h-1.8M5.6 5.6l1.3 1.3M17.1 17.1l1.3 1.3M18.4 5.6l-1.3 1.3M6.9 17.1l-1.3 1.3" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 1 0 10.5 10.5z" />
    </svg>
  );
}
