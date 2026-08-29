"use client";

import { ThemeToggle } from "./ThemeToggle";
import { LangToggle } from "./LangToggle";

/**
 * The left half is page-owned in the vanilla console (`shell.js`'s
 * `PAGES` table rebuilding `#top-left` per route) — Phase 1 has no page
 * content to put there yet (Memory/Journal/Settings are empty stubs), so
 * each page renders its own header into this slot once Phases 2-4 land
 * (`<div id="top-left">` equivalent: a page can portal/compose into here
 * via its own layout, kept simple for now as page-local content instead).
 * The right half carries language + theme only (see `ThemeToggle.tsx`'s
 * docstring for why they live here at all) — connection state does NOT
 * live here (user decision, 2026-08-29): it moved to the sidebar footer's
 * `WsStatusIndicator`, the same spot the vanilla console's machine facts
 * used, so the topbar never says anything about the machine.
 */
export function Topbar() {
  return (
    <header className="topbar">
      <div className="top-left" />
      <div className="top-right">
        <LangToggle />
        <ThemeToggle />
      </div>
    </header>
  );
}
