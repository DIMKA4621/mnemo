import { create } from "zustand";
import type { ThemeMode } from "../theme/design-tokens";

// Same three flat, non-JSON-wrapped localStorage keys the vanilla console
// used (`src/webui/static/app.js` / `shell.js`) — a browser that already has
// a saved preference from before the rewrite keeps it. Deliberately NOT
// zustand's `persist` middleware: that wraps the whole store in one JSON
// envelope under one key, which would silently orphan these legacy keys.
const THEME_KEY = "mnemo_theme";
const LANG_KEY = "mnemo_lang";
const SIDEBAR_KEY = "mnemo_sidebar";

export type Lang = "en" | "uk";

function readTheme(): ThemeMode {
  if (typeof window === "undefined") return "dark";
  return window.localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
}

function readLang(): Lang {
  if (typeof window === "undefined") return "en";
  return window.localStorage.getItem(LANG_KEY) === "uk" ? "uk" : "en";
}

function readSidebarCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(SIDEBAR_KEY) === "collapsed";
}

interface UiState {
  theme: ThemeMode;
  lang: Lang;
  sidebarCollapsed: boolean;
  /** True once `hydrate()` has run — before that, `theme`/`lang`/
   *  `sidebarCollapsed` are the static-export defaults, matched to what the
   *  server-rendered HTML shows, so hydration never mismatches. */
  hydrated: boolean;
  setTheme: (theme: ThemeMode) => void;
  setLang: (lang: Lang) => void;
  toggleSidebar: () => void;
  /** Reads the three legacy keys once, client-side, after mount. */
  hydrate: () => void;
}

export const useUiStore = create<UiState>((set, get) => ({
  theme: "dark",
  lang: "en",
  sidebarCollapsed: false,
  hydrated: false,

  setTheme: (theme) => {
    if (typeof window !== "undefined") window.localStorage.setItem(THEME_KEY, theme);
    document.documentElement.dataset.theme = theme;
    set({ theme });
  },

  setLang: (lang) => {
    if (typeof window !== "undefined") window.localStorage.setItem(LANG_KEY, lang);
    document.documentElement.lang = lang;
    set({ lang });
  },

  toggleSidebar: () => {
    const next = !get().sidebarCollapsed;
    if (typeof window !== "undefined") {
      window.localStorage.setItem(SIDEBAR_KEY, next ? "collapsed" : "expanded");
    }
    set({ sidebarCollapsed: next });
  },

  hydrate: () => {
    const theme = readTheme();
    const lang = readLang();
    document.documentElement.dataset.theme = theme;
    document.documentElement.lang = lang;
    set({ theme, lang, sidebarCollapsed: readSidebarCollapsed(), hydrated: true });
  },
}));
