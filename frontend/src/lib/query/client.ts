import { QueryClient } from "@tanstack/react-query";

/**
 * Module-level singleton, not one built inside a component: `lib/ws/
 * dispatch.ts` needs to call `setQueryData`/`invalidateQueries` from a
 * WebSocket callback that lives outside React's render tree entirely. The
 * `QueryClientProvider` in `app/layout.tsx` is handed this same instance
 * rather than constructing its own.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The WS channel is the freshness signal (contract 9.7's `hello` /
      // per-type deltas); a short-lived window avoids a refetch storm from
      // route changes/refocus between two WS events for the same data.
      staleTime: 15_000,
      retry: 1,
    },
  },
});
