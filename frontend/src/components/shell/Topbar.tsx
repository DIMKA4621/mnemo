"use client";

import { ThemeToggle } from "./ThemeToggle";
import { LangToggle } from "./LangToggle";
import { usePageHeaderSlotStore } from "@/lib/store/page-header-slot";

/**
 * The left half is page-owned in the vanilla console (`shell.js`'s
 * `PAGES` table rebuilding `#top-left` per route). React has no built-in
 * cross-component slot injection, so a page composes into this row via a
 * portal targeting this div's DOM node, published through
 * `usePageHeaderSlotStore` by the `ref` callback below — see
 * `MemoryPageHeader.tsx` (Phase 2, MN-34) for the first real user of it.
 * A `ref` callback fires during commit, not render, so publishing through
 * it (rather than a `useEffect` + `document.getElementById` in the
 * consumer) needs no post-mount `setState`-in-effect at all. The right
 * half carries language + theme only (see `ThemeToggle.tsx`'s docstring
 * for why they live here at all) — connection state does NOT live here
 * (user decision, 2026-08-29): it moved to the sidebar footer's
 * `WsStatusIndicator`, the same spot the vanilla console's machine facts
 * used, so the topbar never says anything about the machine.
 * Both controls are icon-sized `.sb-toggle` buttons in one row at one
 * height (`.top-right`, see `shell.css`) — `LangToggle`'s dropdown opens as
 * an overlay, so it never reflows this row.
 */
export function Topbar() {
  return (
    <header className="topbar">
      <div
        className="top-left"
        id="mnemo-page-header-slot"
        ref={(el) => {
          usePageHeaderSlotStore.getState().setSlot(el);
          return () => usePageHeaderSlotStore.getState().setSlot(null);
        }}
      />
      <div className="top-right">
        <LangToggle />
        <ThemeToggle />
      </div>
    </header>
  );
}
