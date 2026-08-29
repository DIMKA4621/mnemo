import { create } from "zustand";

// Contract 9.1. `sessionStorage`, not `localStorage` — session-only by
// design, matching `resolveToken()` in the vanilla console's `app.js`.
const TOKEN_KEY = "mnemo_token";

/**
 * Pulls `?token=` out of the URL once, stores it in `sessionStorage`, scrubs
 * the query param from the address bar, and falls back to whatever is
 * already in `sessionStorage`. Same contract as `resolveToken()` in
 * `src/webui/static/app.js` — must only ever run client-side, after mount
 * (the static-exported HTML has no URL/sessionStorage to read at build
 * time).
 */
function resolveToken(): string {
  if (typeof window === "undefined") return "";
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    window.sessionStorage.setItem(TOKEN_KEY, fromUrl);
    url.searchParams.delete("token");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    return fromUrl;
  }
  return window.sessionStorage.getItem(TOKEN_KEY) || "";
}

/** `missing`: no token presented yet, nothing observed about the service.
 *  `rejected`: a request actually came back 401 — the service said no. */
export type GateReason = "missing" | "rejected" | null;

interface TokenState {
  token: string;
  gateOpen: boolean;
  gateReason: GateReason;
  hydrated: boolean;
  hydrate: () => void;
  setToken: (token: string) => void;
  openGate: (reason: GateReason) => void;
  closeGate: () => void;
}

export const useTokenStore = create<TokenState>((set) => ({
  token: "",
  gateOpen: false,
  gateReason: null,
  hydrated: false,

  hydrate: () => {
    set({ token: resolveToken(), hydrated: true });
  },

  setToken: (token) => {
    if (typeof window !== "undefined") window.sessionStorage.setItem(TOKEN_KEY, token);
    set({ token, gateOpen: false, gateReason: null });
  },

  openGate: (reason) => set({ gateOpen: true, gateReason: reason }),
  closeGate: () => set({ gateOpen: false, gateReason: null }),
}));
