"use client";

import { useEffect, useMemo, useState } from "react";
import { JournalHeader } from "@/components/journal/JournalHeader";
import { JournalFilters, type JournalPeriod } from "@/components/journal/JournalFilters";
import { JournalEventList } from "@/components/journal/JournalEventList";
import { JournalDetailPane } from "@/components/journal/JournalDetailPane";
import { PaneResizer } from "@/components/common/PaneResizer";
import { useBanks } from "@/hooks/useMemoryQueries";
import { useLogs } from "@/hooks/useJournalQueries";
import { useJournalWidthStore } from "@/lib/store/journal-width";
import type { IndexLogEvent, QueryLogEvent } from "@/lib/api/journal";
import "@/components/journal/journal.css";

const PERIOD_HOURS: Record<JournalPeriod, number> = { "1h": 1, "24h": 24, "7d": 24 * 7, "30d": 24 * 30 };

function periodSinceIso(period: JournalPeriod): string {
  return new Date(Date.now() - PERIOD_HOURS[period] * 3600 * 1000).toISOString();
}

export default function JournalPage() {
  const width = useJournalWidthStore((s) => s.width);
  const hydrateWidth = useJournalWidthStore((s) => s.hydrate);
  const beginDrag = useJournalWidthStore((s) => s.beginDrag);
  const applyDrag = useJournalWidthStore((s) => s.applyDrag);
  const commitDrag = useJournalWidthStore((s) => s.commitDrag);

  const [kind, setKind] = useState<"query" | "index">("query");
  const [bank, setBank] = useState("");
  const [period, setPeriod] = useState<JournalPeriod>("24h");
  // Switching the Query/Index segmented control remembers each tab's own
  // last-selected event, rather than one shared selection (ported from the
  // vanilla console's `state.logSelected`).
  const [selectedId, setSelectedId] = useState<{ query: number | null; index: number | null }>({
    query: null,
    index: null,
  });

  useEffect(() => {
    hydrateWidth();
  }, [hydrateWidth]);

  const banksQuery = useBanks();
  const banksById = useMemo(
    () => new Map((banksQuery.data ?? []).map((b) => [b.id, b.name])),
    [banksQuery.data],
  );
  function bankLabel(bankId: string): string {
    return banksById.get(bankId) ?? bankId ?? "—";
  }

  // Computed once per `period` change (`useMemo`, not inline) — a fresh
  // `new Date()` on every render would put a millisecond-different `since`
  // into the query key each time, defeating caching and re-fetching in a
  // tight loop (caught live while verifying against the real backend).
  const since = useMemo(() => periodSinceIso(period), [period]);
  const logsQuery = useLogs<QueryLogEvent | IndexLogEvent>({
    kind,
    bank: bank || undefined,
    since,
  });

  const events = logsQuery.data?.events ?? [];
  const total = logsQuery.data?.total ?? 0;
  const selected = events.find((e) => e.id === selectedId[kind]) ?? events[0] ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <JournalHeader kind={kind} onKindChange={setKind} onRefresh={() => logsQuery.refetch()} />
      <JournalFilters bank={bank} onBankChange={setBank} period={period} onPeriodChange={setPeriod} />
      <div className="jl" style={{ gridTemplateColumns: `${width}px 6px minmax(0, 1fr)` }}>
        <JournalEventList
          kind={kind}
          events={events}
          total={total}
          loading={logsQuery.isLoading}
          selectedId={selected?.id ?? null}
          onSelect={(id) => setSelectedId((prev) => ({ ...prev, [kind]: id }))}
          bankLabel={bankLabel}
        />
        <PaneResizer onStart={beginDrag} onDrag={applyDrag} onCommit={commitDrag} />
        <JournalDetailPane kind={kind} event={selected} bankLabel={bankLabel} />
      </div>
    </div>
  );
}
