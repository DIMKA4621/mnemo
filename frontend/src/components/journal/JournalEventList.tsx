"use client";

import { useT } from "@/lib/i18n/hooks";
import { EventCard } from "./EventCard";
import type { IndexLogEvent, QueryLogEvent } from "@/lib/api/journal";

interface JournalEventListProps {
  kind: "query" | "index";
  events: (QueryLogEvent | IndexLogEvent)[];
  total: number;
  loading: boolean;
  selectedId: number | null;
  onSelect: (id: number) => void;
  bankLabel: (bankId: string) => string;
}

export function JournalEventList({
  kind,
  events,
  total,
  loading,
  selectedId,
  onSelect,
  bankLabel,
}: JournalEventListProps) {
  const t = useT();

  return (
    <div className="events">
      <div className="events-head">
        <span>{t("journal.list.newestFirst")}</span>
        <span>
          {loading
            ? t("memory.tree.loading")
            : events.length
              ? t("journal.list.shownOf", { shown: events.length, total })
              : t("journal.list.empty")}
        </span>
      </div>
      <div className="events-body">
        {!loading && events.length === 0 && <p className="empty-hint">{t("journal.list.noEvents")}</p>}
        {events.map((ev) => (
          <EventCard
            key={ev.id}
            kind={kind}
            event={ev}
            bankName={bankLabel(ev.bank_id)}
            selected={ev.id === selectedId}
            onSelect={() => onSelect(ev.id)}
          />
        ))}
      </div>
    </div>
  );
}
