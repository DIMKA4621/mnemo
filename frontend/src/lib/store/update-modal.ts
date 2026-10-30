import { create } from "zustand";

/** `'idle'` (not shown) | manual: `'confirm'` -> `'progress'` -> `'terminal'`
 *  | auto: `'auto-pending'` -> `'progress'` -> `'terminal'` | `'timeout'` is
 *  an escape hatch out of `'progress'` when the poll budget runs out. Ported
 *  from the vanilla console's `updateModal.phase` (`update.js`). */
export type UpdateModalPhase = "idle" | "confirm" | "auto-pending" | "progress" | "timeout" | "terminal";

interface UpdateModalState {
  phase: UpdateModalPhase;
  // Observed, this session, that staging finished and handoff to the
  // detached `update-apply` began — the point past which a "failed" outcome
  // means the switch itself was attempted, not just staging. Wording only;
  // no phase transition depends on it.
  everSwitching: boolean;
  // The auto-pending phase's own confirm/cancel error, separate from
  // anything the progress/terminal phases show.
  autoPendingError: string | null;
  setPhase: (phase: UpdateModalPhase) => void;
  setEverSwitching: (v: boolean) => void;
  setAutoPendingError: (msg: string | null) => void;
}

/**
 * Plain setters only, same shape as `lib/store/index-progress.ts`'s
 * `upsert`/`clear` — the state machine's actual sequencing (which
 * transition follows which event) lives in `UpdateModal.tsx` and
 * `lib/ws/dispatch.ts`'s `update_progress`/`update_auto_pending` handlers,
 * both of which call `.getState()` on this store directly (the same
 * from-outside-React pattern `dispatch.ts` already uses for indexing
 * progress), not inside it — keeping the guards next to the events that
 * trigger them, the same place the vanilla console's `update.js` put them.
 */
export const useUpdateModalStore = create<UpdateModalState>((set) => ({
  phase: "idle",
  everSwitching: false,
  autoPendingError: null,

  setPhase: (phase) => set({ phase }),
  setEverSwitching: (v) => set({ everSwitching: v }),
  setAutoPendingError: (msg) => set({ autoPendingError: msg }),
}));
