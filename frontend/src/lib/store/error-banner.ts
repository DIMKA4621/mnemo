import { create } from "zustand";

/**
 * One page-level banner for unhandled API failures (contract: any query's
 * `onError` can call `showError()`). Phase 1 only wires the store and the
 * component that renders it — real call sites land as each page's queries
 * are built in Phases 2-4, matching `dispatch.ts`'s own "infra now, wiring
 * later" split.
 */
interface ErrorBannerState {
  message: string | null;
  showError: (message: string) => void;
  hide: () => void;
}

export const useErrorBannerStore = create<ErrorBannerState>((set) => ({
  message: null,
  showError: (message) => set({ message }),
  hide: () => set({ message: null }),
}));
