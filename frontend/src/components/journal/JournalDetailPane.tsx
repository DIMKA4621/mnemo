"use client";

import Link from "next/link";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { StatusBadge } from "@/components/common/StatusBadge";
import { fmtDateTime, fmtMs } from "@/lib/memory/format";
import { HitRow } from "./HitRow";
import { indexEventTitleKey, indexResultKey, indexStatusVariant, queryStatusVariant } from "./eventStatus";
import type { IndexLogEvent, QueryLogEvent } from "@/lib/api/journal";

function FactsRow({ pairs }: { pairs: [string, string][] }) {
  return (
    <div className="d-facts">
      {pairs.map(([label, value]) => (
        <div key={label}>
          <div className="d-k">{label}</div>
          <div className="d-v">{value}</div>
        </div>
      ))}
    </div>
  );
}

function NumBox({ value, label }: { value: string | number; label: string }) {
  return (
    <div>
      <div className="num">{value}</div>
      <div className="num-k">{label}</div>
    </div>
  );
}

function QueryDetail({ ev, bankName }: { ev: QueryLogEvent; bankName: string }) {
  const t = useT();
  return (
    <div className="detail-in">
      <div className="d-kick">
        <span>{t("journal.detail.queryKicker", { id: ev.id })}</span>
        <StatusBadge variant={queryStatusVariant(ev)} text={t(`common.status.${ev.status}`)} />
      </div>
      <h2 className="d-h">{ev.query}</h2>
      <FactsRow
        pairs={[
          [t("journal.detail.bank"), bankName],
          [t("journal.detail.face"), ev.face],
          [t("journal.detail.prefix"), ev.path_prefix || "—"],
          [t("journal.detail.hits"), String(ev.n_hits)],
          [t("journal.detail.tookMs"), fmtMs(ev.took_ms)],
          [t("journal.detail.when"), fmtDateTime(ev.ts)],
        ]}
      />
      <div className="d-sec">
        {t("journal.detail.resultsLabel")}{" "}
        <span className="muted">{t("journal.detail.resultsOrderNote")}</span>
      </div>
      {ev.hits.length === 0 ? (
        <p className="empty-hint">{t("journal.detail.noHits")}</p>
      ) : (
        ev.hits.map((hit, i) => <HitRow key={hit.chunk_uid} hit={hit} index={i} bankId={ev.bank_id} />)
      )}
    </div>
  );
}

function IndexDetail({ ev, bankName }: { ev: IndexLogEvent; bankName: string }) {
  const t = useT();
  const titleKey = indexEventTitleKey(ev);
  const title = titleKey ? t(titleKey) : (ev.path as string);
  const fileHref = ev.path
    ? `/memory?bank=${encodeURIComponent(ev.bank_id)}&path=${encodeURIComponent(ev.path)}`
    : null;

  return (
    <div className="detail-in">
      <div className="d-kick">
        <span>{t("journal.detail.indexKicker", { id: ev.id })}</span>
        <StatusBadge variant={indexStatusVariant(ev)} text={t(indexResultKey(ev))} />
      </div>
      <h2 className="d-h">{title}</h2>
      <FactsRow
        pairs={[
          [t("journal.detail.bank"), bankName],
          [t("journal.detail.kind"), ev.kind],
          [t("journal.detail.trigger"), ev.trigger],
          [t("journal.detail.when"), fmtDateTime(ev.ts)],
        ]}
      />
      <div className="nums">
        <NumBox value={ev.files_indexed} label={t("journal.detail.filesIndexed")} />
        <NumBox value={ev.chunks_indexed} label={t("journal.detail.chunksIndexed")} />
        <NumBox value={ev.files_pruned} label={t("journal.detail.filesPruned")} />
        <NumBox value={fmtMs(ev.took_ms)} label={t("journal.detail.duration")} />
      </div>

      {ev.error && (
        <div className="note is-err">
          <strong>{t("journal.detail.errorLabel")}</strong>
          <br />
          {ev.error}
        </div>
      )}

      {ev.path && (
        <>
          <div className="d-sec">{t("journal.detail.fileSection")}</div>
          <article className="hit">
            <div className="hit-top">
              <span className="hit-r">·</span>
              <div className="hit-l">
                <div className="hit-p">{ev.path}</div>
                <div className="hit-h">{t("journal.detail.currentFileOf", { bank: bankName })}</div>
              </div>
            </div>
            <div className="hit-foot">
              <Link href={fileHref as string}>
                <Button size="small">{t("journal.hit.openFile")}</Button>
              </Link>
            </div>
          </article>
        </>
      )}
    </div>
  );
}

interface JournalDetailPaneProps {
  kind: "query" | "index";
  event: QueryLogEvent | IndexLogEvent | null;
  bankLabel: (bankId: string) => string;
}

export function JournalDetailPane({ kind, event, bankLabel }: JournalDetailPaneProps) {
  const t = useT();

  if (!event) {
    return (
      <div className="detail">
        <p className="empty-hint">{t("journal.detail.selectHint")}</p>
      </div>
    );
  }

  return (
    <div className="detail">
      {kind === "query" ? (
        <QueryDetail ev={event as QueryLogEvent} bankName={bankLabel(event.bank_id)} />
      ) : (
        <IndexDetail ev={event as IndexLogEvent} bankName={bankLabel(event.bank_id)} />
      )}
    </div>
  );
}
