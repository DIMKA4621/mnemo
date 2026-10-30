"use client";

import { useQuery } from "@tanstack/react-query";
import { queryKeys, type LogFilters } from "@/lib/query/keys";
import { getLogs, type IndexLogEvent, type QueryLogEvent } from "@/lib/api/journal";

/** `filters.kind` decides the element type at the call site — narrowed via
 *  the generic rather than a union return, so `JournalEventList`/
 *  `JournalDetailPane` don't each re-discriminate `kind === "query"`. */
export function useLogs<E = QueryLogEvent | IndexLogEvent>(filters: LogFilters) {
  return useQuery({
    queryKey: queryKeys.logs.list(filters),
    queryFn: () =>
      getLogs<E>({
        kind: filters.kind ?? "query",
        bank: filters.bank,
        since: filters.since,
        until: filters.until,
      }),
    enabled: !!filters.kind,
  });
}
