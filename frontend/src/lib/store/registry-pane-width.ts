import { create } from "zustand";

const KEY = "mnemo_registry_pane_width";

export const REGISTRY_WIDTH_MIN = 240;
export const REGISTRY_WIDTH_MAX = 560;
export const REGISTRY_WIDTH_DEFAULT = 320;

function clamp(px: number): number {
  return Math.min(REGISTRY_WIDTH_MAX, Math.max(REGISTRY_WIDTH_MIN, Math.round(px)));
}

function readWidth(): number {
  if (typeof window === "undefined") return REGISTRY_WIDTH_DEFAULT;
  const raw = Number(window.localStorage.getItem(KEY));
  return Number.isFinite(raw) && raw > 0 ? clamp(raw) : REGISTRY_WIDTH_DEFAULT;
}

// The width the one draggable divider started a gesture from — plain module
// state rather than `useRef`, same reasoning as `pane-widths.ts`'s `dragBase`.
let dragBase = REGISTRY_WIDTH_DEFAULT;

interface RegistryPaneWidthState {
  width: number;
  hydrated: boolean;
  /** Reads `localStorage` once, post-mount — same `hydrated` pattern as
   *  `journal-width.ts`, so the first paint matches the static-export
   *  default and hydration never mismatches. */
  hydrate: () => void;
  beginDrag: () => void;
  applyDrag: (deltaX: number) => void;
  commitDrag: () => void;
}

export const useRegistryPaneWidthStore = create<RegistryPaneWidthState>((set, get) => ({
  width: REGISTRY_WIDTH_DEFAULT,
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
