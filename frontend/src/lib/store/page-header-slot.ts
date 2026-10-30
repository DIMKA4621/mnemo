import { create } from "zustand";

interface PageHeaderSlotState {
  slot: HTMLElement | null;
  setSlot: (slot: HTMLElement | null) => void;
}

/**
 * The shell's persistent `Topbar` (`components/shell/Topbar.tsx`) exposes
 * its `.top-left` DOM node here via a `ref` callback — not a `useEffect`.
 * Refs commit before a page's own header component even needs to know
 * about them, and a `ref` callback isn't subject to the "no setState in an
 * effect" lint rule the way a `useEffect`+`useState` pair reading
 * `document.getElementById()` after mount would be. A page header (e.g.
 * `MemoryPageHeader`) reads `slot` from this store and portals into it once
 * it's set — one extra render after mount, same as any other store-fed
 * value, no manual DOM lookup involved.
 */
export const usePageHeaderSlotStore = create<PageHeaderSlotState>((set) => ({
  slot: null,
  setSlot: (slot) => set({ slot }),
}));
