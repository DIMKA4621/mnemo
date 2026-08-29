import type { QueryClient } from "@tanstack/react-query";
import { queryClient } from "../query/client";
import { queryKeys } from "../query/keys";

/** Contract 9.7's envelope shape. */
export interface WsEnvelope<T = Record<string, unknown>> {
  v?: number;
  type: string;
  ts?: string;
  bank_id?: string | null;
  data?: T;
}

type Handler = (envelope: WsEnvelope, qc: QueryClient) => void;

// `hello` follows the very first connect with nothing to resync (REST just
// loaded everything); every later `hello` means the socket dropped and
// deltas were missed, so contract 9.7 says it means "refetch everything" —
// the same semantic `resyncAll()` implements in the vanilla console
// (`src/webui/static/shell.js`).
let helloSeen = false;

/** Reset between WS client lifecycles (tests, hot reload) — otherwise a
 *  fresh `connectSocket()` call would treat its own first `hello` as a
 *  resync. */
export function resetHelloSeen() {
  helloSeen = false;
}

/**
 * One handler per `/ws` event type (contract 9.7). Most are stubs for now —
 * Phase 1 is infrastructure only, the real `setQueryData`/
 * `invalidateQueries` calls land as each page's own queries are built in
 * Phases 2-4 (see the plan's "Архітектура стану" section for the intended
 * reconciliation per type). `hello` and the handful of broadly-applicable
 * bank/tree invalidations are wired now because they need no page-specific
 * query shape to be correct.
 */
const handlers: Record<string, Handler> = {
  hello: (_envelope, qc) => {
    if (helloSeen) qc.invalidateQueries();
    helloSeen = true;
  },

  // Sent on every queue-depth change; Phase 2's bank cards read this.
  queue: () => {},

  // Per-bank indexing progress — Phase 2 (bank card progress bar).
  index_start: () => {},
  index_progress: () => {},
  index_yield: () => {},
  index_done: (envelope, qc) => {
    qc.invalidateQueries({ queryKey: queryKeys.banks.all });
    if (envelope.bank_id) qc.invalidateQueries({ queryKey: queryKeys.tree.bank(envelope.bank_id) });
  },
  index_error: (_envelope, qc) => {
    qc.invalidateQueries({ queryKey: queryKeys.banks.all });
  },

  // A file/prune task was (re-)queued — Phase 2 (tree row highlight).
  file_queued: () => {},
  prune: (envelope, qc) => {
    if (envelope.bank_id) qc.invalidateQueries({ queryKey: queryKeys.tree.bank(envelope.bank_id) });
  },

  bank_added: (_envelope, qc) => qc.invalidateQueries({ queryKey: queryKeys.banks.all }),
  bank_removed: (_envelope, qc) => qc.invalidateQueries({ queryKey: queryKeys.banks.all }),
  bank_status: (_envelope, qc) => qc.invalidateQueries({ queryKey: queryKeys.banks.all }),

  // Self-update — machine-level, bank_id is always null. Phase 4 (Settings).
  update_progress: () => {},
  update_auto_pending: () => {},

  // A live query just happened — Phase 3 (Journal live feed).
  query: (_envelope, qc) => qc.invalidateQueries({ queryKey: queryKeys.logs.all }),
};

/**
 * Routes one parsed `/ws` envelope. `ping`/`pong` are handled by the
 * transport itself (`lib/ws/client.ts`, which owns the socket instance
 * needed to reply) and never reach here. An unknown `type` is a no-op on
 * purpose — contract 9.7's forward-compat guarantee: the backend can add
 * event types without breaking a client that hasn't learned them yet.
 */
export function dispatchEvent(envelope: WsEnvelope): void {
  const handler = handlers[envelope.type];
  if (!handler) return;
  handler(envelope, queryClient);
}
