"use client";

import { useT } from "@/lib/i18n/hooks";
import { StatusBadge } from "@/components/common/StatusBadge";
import { fmtDateTime } from "@/lib/memory/format";
import type { IndexLogEvent, QueryLogEvent } from "@/lib/api/journal";
import { indexEventTitleKey, indexResultKey, indexStatusVariant, queryStatusVariant } from "./eventStatus";

type T = ReturnType<typeof useT>;

// Numbers, not strings, so the message formats the decimal separator per locale.
function fmtDuration(t: T, ms: number): string {
  if (ms < 1000) return t("journal.event.ms", { n: ms < 10 ? Math.round(ms * 10) / 10 : Math.round(ms) });
  return t("journal.event.s", { n: Math.round(ms / 100) / 10 });
}

/** Time only for today's events, date and time otherwise. */
function fmtWhen(iso: string): string {
  const full = fmtDateTime(iso);
  const d = new Date(iso);
  return d.toDateString() === new Date().toDateString() ? full.slice(11) : full;
}

interface EventCardProps {
  kind: "query" | "index";
  event: QueryLogEvent | IndexLogEvent;
  bankName: string;
  selected: boolean;
  onSelect: () => void;
}

/** One `.ev` card — a query or an index event read as its own two-line
 *  block, not a table row (ported layout, `page-journal.js`'s `evCard`). */
export function EventCard({ kind, event, bankName, selected, onSelect }: EventCardProps) {
  const t = useT();

  let title: string;
  let count: string;
  let metaWord: string;
  let variant: ReturnType<typeof queryStatusVariant>;
  let statusWord: string;

  if (kind === "query") {
    const ev = event as QueryLogEvent;
    title = ev.query;
    count = t("journal.event.hits", { n: ev.n_hits });
    metaWord = ev.face;
    variant = queryStatusVariant(ev);
    statusWord = t(`common.status.${ev.status}`);
  } else {
    const ev = event as IndexLogEvent;
    const titleKey = indexEventTitleKey(ev);
    title = titleKey ? t(titleKey) : (ev.path as string);
    count = t("memory.count.chunks", { n: ev.chunks_indexed });
    metaWord = ev.trigger;
    variant = indexStatusVariant(ev);
    statusWord = t(indexResultKey(ev));
  }

  return (
    <button type="button" className={`ev${selected ? " is-selected" : ""}`} onClick={onSelect}>
      <div className="ev-top">
        <span className="ev-q">{title}</span>
        <span className="ev-n">
          {count} · {fmtDuration(t, event.took_ms)}
        </span>
      </div>
      <div className="ev-bot">
        <StatusBadge variant={variant} text={statusWord} />
        <span>{bankName}</span>
        <span>{metaWord || "—"}</span>
        <span className="t">{fmtWhen(event.ts)}</span>
      </div>
    </button>
  );
}
