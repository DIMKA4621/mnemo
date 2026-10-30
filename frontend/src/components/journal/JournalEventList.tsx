"use client";

import { Alert, Skeleton } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { EventCard } from "./EventCard";
import type { IndexLogEvent, QueryLogEvent } from "@/lib/api/journal";

interface JournalEventListProps {
  kind: "query" | "index";
  events: (QueryLogEvent | IndexLogEvent)[];
  total: number;
  loading: boolean;
  error: string | null;
  selectedId: number | null;
  onSelect: (id: number) => void;
  bankLabel: (bankId: string) => string;
}

export function JournalEventList({
  kind,
  events,
  total,
  loading,
  error,
  selectedId,
  onSelect,
  bankLabel,
}: JournalEventListProps) {
  const t = useT();

  return (
    <div className="events">
      <div className="events-head">
        <h2>{t("journal.list.caption")}</h2>
        <span className="events-count">
          {loading
            ? t("memory.tree.loading")
            : error
              ? "—"
              : events.length
              ? t("journal.list.shownOf", { shown: events.length, total })
              : t("journal.list.empty")}
        </span>
      </div>
      <div className="events-body">
        {loading && <Skeleton active title={false} paragraph={{ rows: 6 }} />}
        {error && <Alert type="error" showIcon title={error} />}
        {!loading && !error && events.length === 0 && <p className="mnemo-empty-hint">{t("journal.list.noEvents")}</p>}
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
