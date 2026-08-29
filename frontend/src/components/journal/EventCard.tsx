"use client";

import { useT } from "@/lib/i18n/hooks";
import { StatusBadge } from "@/components/common/StatusBadge";
import { fmtDateTime, fmtMs } from "@/lib/memory/format";
import type { IndexLogEvent, QueryLogEvent } from "@/lib/api/journal";
import { indexEventTitleKey, indexResultKey, indexStatusVariant, queryStatusVariant } from "./eventStatus";

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
  let n: number;
  let metaWord: string;
  let variant: ReturnType<typeof queryStatusVariant>;
  let statusWord: string;

  if (kind === "query") {
    const ev = event as QueryLogEvent;
    title = ev.query;
    n = ev.n_hits;
    metaWord = ev.face;
    variant = queryStatusVariant(ev);
    statusWord = t(`common.status.${ev.status}`);
  } else {
    const ev = event as IndexLogEvent;
    const titleKey = indexEventTitleKey(ev);
    title = titleKey ? t(titleKey) : (ev.path as string);
    n = ev.chunks_indexed;
    metaWord = ev.trigger;
    variant = indexStatusVariant(ev);
    statusWord = t(indexResultKey(ev));
  }

  return (
    <button type="button" className={`ev${selected ? " is-selected" : ""}`} onClick={onSelect}>
      <div className="ev-top">
        <span className="ev-q">{title}</span>
        <span className="ev-n">
          {n}
          <small>{fmtMs(event.took_ms)}</small>
        </span>
      </div>
      <div className="ev-bot">
        <StatusBadge variant={variant} text={statusWord} />
        <span>{bankName}</span>
        <span>{metaWord || "—"}</span>
        <span className="t">{fmtDateTime(event.ts)}</span>
      </div>
    </button>
  );
}
