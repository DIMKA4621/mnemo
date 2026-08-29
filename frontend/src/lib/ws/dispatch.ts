import type { QueryClient } from "@tanstack/react-query";
import { queryClient } from "../query/client";
import { queryKeys } from "../query/keys";
import { useIndexProgressStore } from "../store/index-progress";
import type { StatusResult, TreeResult } from "../api/memory";

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

  // Sent on every queue-depth change. Patched directly rather than
  // invalidated: `queue` fires faster than `index_progress`'s 200ms
  // throttle, and a `useStatus()` that refetched on every one would hammer
  // `/api/status`. `BankCard`'s live "queued N"/"indexing" reads
  // `status.queue.by_bank`, never `BankInfo.queued`/`indexing` (those are a
  // fetch-time snapshot only).
  queue: (envelope, qc) => {
    qc.setQueryData<StatusResult>(queryKeys.status.all, (old) =>
      old ? { ...old, queue: envelope.data as unknown as StatusResult["queue"] } : old,
    );
  },

  // Per-bank indexing progress, upserted into `lib/store/index-progress.ts`
  // (outside the query cache — a stream of transient deltas, not a
  // cacheable resource). `FileTree` reads it for a live progress row per
  // path; `index_done`/`index_error` below clear the matching entry, or a
  // finished file's progress row never disappears.
  index_start: (envelope) => {
    if (!envelope.bank_id) return;
    const data = envelope.data as { task_id?: string | number; path?: string; batches?: number };
    if (!data.path) return;
    useIndexProgressStore.getState().upsert(envelope.bank_id, {
      taskId: data.task_id ?? null,
      path: data.path,
      batch: 0,
      batches: data.batches ?? 0,
      state: "running",
    });
  },
  index_progress: (envelope) => {
    if (!envelope.bank_id) return;
    const data = envelope.data as {
      task_id?: string | number;
      path?: string;
      batch?: number;
      batches?: number;
      chunks_done?: number;
      chunks_total?: number;
    };
    if (!data.path) return;
    useIndexProgressStore.getState().upsert(envelope.bank_id, {
      taskId: data.task_id ?? null,
      path: data.path,
      batch: data.batch ?? 0,
      batches: data.batches ?? 0,
      chunksDone: data.chunks_done,
      chunksTotal: data.chunks_total,
      state: "running",
    });
  },
  index_yield: (envelope) => {
    if (!envelope.bank_id) return;
    const data = envelope.data as { task_id?: string | number; path?: string };
    if (!data.path) return;
    const store = useIndexProgressStore.getState();
    const existing = store.byBank.get(envelope.bank_id)?.get(data.path);
    store.upsert(envelope.bank_id, {
      taskId: data.task_id ?? existing?.taskId ?? null,
      path: data.path,
      batch: existing?.batch ?? 0,
      batches: existing?.batches ?? 0,
      chunksDone: existing?.chunksDone,
      chunksTotal: existing?.chunksTotal,
      state: "yielded",
    });
  },
  index_done: (envelope, qc) => {
    qc.invalidateQueries({ queryKey: queryKeys.banks.all });
    if (envelope.bank_id) qc.invalidateQueries({ queryKey: queryKeys.tree.bank(envelope.bank_id) });
    // Journal's Indexing tab lives on the same `GET /api/logs` this event
    // just added a row to (a `prune` task always emits `index_done(kind=
    // "prune")` right after, so this alone covers prune's row too — see
    // `workqueue.py` around its `_emit("prune", ...)` call).
    qc.invalidateQueries({ queryKey: queryKeys.logs.all });
    const data = envelope.data as { path?: string };
    if (envelope.bank_id && data.path) {
      useIndexProgressStore.getState().clear(envelope.bank_id, data.path);
    }
  },
  index_error: (envelope, qc) => {
    qc.invalidateQueries({ queryKey: queryKeys.banks.all });
    qc.invalidateQueries({ queryKey: queryKeys.logs.all });
    const data = envelope.data as { path?: string };
    if (envelope.bank_id && data.path) {
      useIndexProgressStore.getState().clear(envelope.bank_id, data.path);
    }
  },

  // A file/prune task was (re-)queued (`workqueue._emit("file_queued",
  // bank_id, path=task.path)` — confirmed from `src/workqueue.py:398`, data
  // shape is `{path: string}`). Folded into the tree's own `pending` list so
  // `FileTree` highlights it immediately, the same field `GET /api/tree`
  // seeds at page-open time.
  file_queued: (envelope, qc) => {
    if (!envelope.bank_id) return;
    const data = envelope.data as { path?: string };
    if (!data.path) return;
    const path = data.path;
    qc.setQueryData<TreeResult>(queryKeys.tree.bank(envelope.bank_id), (old) => {
      if (!old) return old;
      if (old.pending.includes(path)) return old;
      return { ...old, pending: [...old.pending, path] };
    });
  },
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
