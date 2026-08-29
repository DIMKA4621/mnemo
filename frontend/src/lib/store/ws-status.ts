import { create } from "zustand";

/** Mirrors the four connection states `shell.js`'s `setConnState()` renders
 *  (`open` / `wait` / `error` / `idle`) — `idle` covers both "never
 *  connected yet" and "gated, deliberately not connecting". */
export type WsStatusKind = "open" | "connecting" | "error" | "idle";

interface WsStatusState {
  kind: WsStatusKind;
  setStatus: (kind: WsStatusKind) => void;
}

export const useWsStatusStore = create<WsStatusState>((set) => ({
  kind: "idle",
  setStatus: (kind) => set({ kind }),
}));
