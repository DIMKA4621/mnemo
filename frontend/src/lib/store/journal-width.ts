import { create } from "zustand";

const KEY = "mnemo_journal_width";

export const JOURNAL_WIDTH_MIN = 260;
export const JOURNAL_WIDTH_MAX = 720;
export const JOURNAL_WIDTH_DEFAULT = 400;

function clamp(px: number): number {
  return Math.min(JOURNAL_WIDTH_MAX, Math.max(JOURNAL_WIDTH_MIN, Math.round(px)));
}

function readWidth(): number {
  if (typeof window === "undefined") return JOURNAL_WIDTH_DEFAULT;
  const raw = Number(window.localStorage.getItem(KEY));
  return Number.isFinite(raw) && raw > 0 ? clamp(raw) : JOURNAL_WIDTH_DEFAULT;
}

// The width the one draggable divider started a gesture from — plain module
// state rather than `useRef`, same reasoning as `pane-widths.ts`'s `dragBase`:
// it exists only for the duration of one mouse gesture and never itself
// drives a render.
let dragBase = JOURNAL_WIDTH_DEFAULT;

interface JournalWidthState {
  width: number;
  hydrated: boolean;
  /** Reads `localStorage` once, post-mount — same `hydrated` pattern as
   *  `pane-widths.ts`, so the first paint matches the static-export default
   *  and hydration never mismatches. */
  hydrate: () => void;
  beginDrag: () => void;
  applyDrag: (deltaX: number) => void;
  commitDrag: () => void;
}

export const useJournalWidthStore = create<JournalWidthState>((set, get) => ({
  width: JOURNAL_WIDTH_DEFAULT,
  hydrated: false,

  hydrate: () => set({ width: readWidth(), hydrated: true }),

  beginDrag: () => {
    dragBase = get().width;
  },

  applyDrag: (deltaX) => {
    set({ width: clamp(dragBase + deltaX) });
  },

  commitDrag: () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(KEY, String(get().width));
    }
  },
}));
