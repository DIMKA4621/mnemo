import { create } from "zustand";

const KEY = "mnemo_pane_widths";

export const PANE_WIDTH_MIN = 180;
export const PANE_WIDTH_MAX = 640;
// The frozen mockup's own live layout (369 Банки / 484 Файли), not an
// arbitrary number — see `.claude/memory/topics/console-ui.md`'s
// 2026-08-20 entry.
export const PANE_WIDTH_DEFAULT: [number, number] = [369, 484];

function clamp(px: number): number {
  return Math.min(PANE_WIDTH_MAX, Math.max(PANE_WIDTH_MIN, Math.round(px)));
}

function readWidths(): [number, number] {
  if (typeof window === "undefined") return PANE_WIDTH_DEFAULT;
  try {
    const raw = JSON.parse(window.localStorage.getItem(KEY) || "null");
    if (Array.isArray(raw) && raw.length === 2 && Number.isFinite(raw[0]) && Number.isFinite(raw[1])) {
      return [clamp(raw[0]), clamp(raw[1])];
    }
  } catch {
    // corrupt or absent — fall through to the default
  }
  return PANE_WIDTH_DEFAULT;
}

// The width each pane started a drag from — plain module state rather than
// a `useRef`: it exists only for the duration of one mouse gesture and
// never itself drives a render, so it lives outside React entirely (same
// reasoning `dialog.tsx`-style stores use `let` fields beside `create()`).
let dragBase: [number, number] = PANE_WIDTH_DEFAULT;

interface PaneWidthsState {
  widths: [number, number];
  hydrated: boolean;
  /** Reads `localStorage` once, post-mount — same `hydrated` pattern as
   *  `lib/store/ui.ts`, so the very first paint matches the static-export
   *  default and hydration never mismatches. */
  hydrate: () => void;
  beginDrag: () => void;
  applyDrag: (index: 0 | 1, deltaX: number) => void;
  commitDrag: () => void;
}

export const usePaneWidthsStore = create<PaneWidthsState>((set, get) => ({
  widths: PANE_WIDTH_DEFAULT,
  hydrated: false,

  hydrate: () => set({ widths: readWidths(), hydrated: true }),

  beginDrag: () => {
    dragBase = get().widths;
  },

  applyDrag: (index, deltaX) => {
    const next: [number, number] = [...get().widths];
    next[index] = clamp(dragBase[index] + deltaX);
    set({ widths: next });
  },

  commitDrag: () => {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(KEY, JSON.stringify(get().widths));
    }
  },
}));
