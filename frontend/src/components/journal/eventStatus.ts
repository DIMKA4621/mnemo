import type { IndexLogEvent, QueryLogEvent } from "@/lib/api/journal";
import type { StatusBadgeVariant } from "@/components/common/StatusBadge";

/** Shared between `EventCard` (the list) and `JournalDetailPane` (the
 *  kicker badge) so the two never drift on what counts as "error" vs
 *  "empty" vs "ready" for one event. */
export function queryStatusVariant(ev: QueryLogEvent): StatusBadgeVariant {
  if (ev.status === "indexing") return "indexing";
  if (ev.status === "empty") return "empty";
  return "ready";
}

export function indexStatusVariant(ev: IndexLogEvent): StatusBadgeVariant {
  if (ev.result === "error") return "off";
  if (ev.result === "skipped") return "empty";
  return "ready";
}

export function indexResultKey(ev: IndexLogEvent): string {
  if (ev.result === "error") return "journal.event.errorStatus";
  return `journal.event.result.${ev.result}`;
}

export function indexEventTitleKey(ev: IndexLogEvent): string | null {
  if (ev.path) return null; // caller renders `ev.path` directly, no i18n key
  if (ev.kind === "rebuild") return "journal.event.rebuildTitle";
  if (ev.kind === "prune") return "journal.event.pruneTitle";
  return "journal.event.syncTitle";
}
