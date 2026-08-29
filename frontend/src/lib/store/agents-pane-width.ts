import { create } from "zustand";

const KEY = "mnemo_agents_pane_width";

// Same min/max as the live-verified mockup's `wireResizer()`
// (`.claude/scratch/agents-page-mockup/app.js`).
export const AGENTS_WIDTH_MIN = 200;
export const AGENTS_WIDTH_MAX = 520;
export const AGENTS_WIDTH_DEFAULT = 300;

function clamp(px: number): number {
  return Math.min(AGENTS_WIDTH_MAX, Math.max(AGENTS_WIDTH_MIN, Math.round(px)));
}

function readWidth(): number {
  if (typeof window === "undefined") return AGENTS_WIDTH_DEFAULT;
  const raw = Number(window.localStorage.getItem(KEY));
  return Number.isFinite(raw) && raw > 0 ? clamp(raw) : AGENTS_WIDTH_DEFAULT;
}

// The width the one draggable divider started a gesture from — plain module
// state rather than `useRef`, same reasoning as `registry-pane-width.ts`.
let dragBase = AGENTS_WIDTH_DEFAULT;

interface AgentsPaneWidthState {
  width: number;
  hydrated: boolean;
  /** Reads `localStorage` once, post-mount — same `hydrated` pattern as
   *  `registry-pane-width.ts`, so the first paint matches the static-export
   *  default and hydration never mismatches. */
  hydrate: () => void;
  beginDrag: () => void;
  applyDrag: (deltaX: number) => void;
  commitDrag: () => void;
}

export const useAgentsPaneWidthStore = create<AgentsPaneWidthState>((set, get) => ({
  width: AGENTS_WIDTH_DEFAULT,
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
