import { create } from "zustand";

/** One in-flight or just-yielded indexing task, as seen live over `/ws`. */
export interface IndexProgressEntry {
  taskId: string | number | null;
  path: string;
  batch: number;
  batches: number;
  chunksDone?: number;
  chunksTotal?: number;
  state: "running" | "yielded";
}

interface IndexProgressState {
  // bankId -> path -> entry
  byBank: Map<string, Map<string, IndexProgressEntry>>;
  upsert: (bankId: string, entry: IndexProgressEntry) => void;
  clear: (bankId: string, path: string) => void;
}

/**
 * Live per-file indexing progress, fed by `lib/ws/dispatch.ts`'s
 * `index_start`/`index_progress`/`index_yield`/`index_done`/`index_error`
 * handlers. Kept out of TanStack Query on purpose — this is a stream of
 * transient deltas, not a cacheable resource, and `FileTree` needs to read
 * one row of it without triggering a refetch of anything.
 */
export const useIndexProgressStore = create<IndexProgressState>((set, get) => ({
  byBank: new Map(),

  upsert: (bankId, entry) => {
    const byBank = new Map(get().byBank);
    const forBank = new Map(byBank.get(bankId) ?? []);
    forBank.set(entry.path, entry);
    byBank.set(bankId, forBank);
    set({ byBank });
  },

  clear: (bankId, path) => {
    const forBank = get().byBank.get(bankId);
    if (!forBank || !forBank.has(path)) return;
    const byBank = new Map(get().byBank);
    const nextForBank = new Map(forBank);
    nextForBank.delete(path);
    byBank.set(bankId, nextForBank);
    set({ byBank });
  },
}));
