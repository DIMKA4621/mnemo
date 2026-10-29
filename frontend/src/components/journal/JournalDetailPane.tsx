"use client";

import { useT } from "@/lib/i18n/hooks";
import { StatusBadge } from "@/components/common/StatusBadge";
import { HitRow } from "./HitRow";
import { FilePathLink } from "./FilePathLink";
import { indexEventTitleKey, indexResultKey, indexStatusVariant, queryStatusVariant } from "./eventStatus";
import type { IndexLogEvent, QueryLogEvent } from "@/lib/api/journal";

/** Only what the event's card does not already show; nothing to add, no row. */
function FactsRow({ pairs }: { pairs: [string, string][] }) {
  if (!pairs.length) return null;
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

function QueryDetail({ ev }: { ev: QueryLogEvent }) {
  const t = useT();
  return (
    <div className="detail-in">
      <div className="d-kick">
        <span>{t("journal.detail.queryKicker", { id: ev.id })}</span>
        <StatusBadge variant={queryStatusVariant(ev)} text={t(`common.status.${ev.status}`)} />
      </div>
      <h2 className="d-h d-q">{ev.query}</h2>
      <FactsRow pairs={ev.path_prefix ? [[t("journal.detail.prefix"), ev.path_prefix]] : []} />
      <div className="d-sec">
        {t("journal.detail.resultsLabel")}{" "}
        <span className="muted">{t("journal.detail.resultsOrderNote")}</span>
      </div>
      {ev.hits.length === 0 ? (
        <p className="mnemo-muted">{t("journal.detail.noHits")}</p>
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

  return (
    <div className="detail-in">
      <div className="d-kick">
        <span>{t("journal.detail.indexKicker", { id: ev.id })}</span>
        <StatusBadge variant={indexStatusVariant(ev)} text={t(indexResultKey(ev))} />
      </div>
      <h2 className="d-h">{title}</h2>
      {/* A rebuild/prune/sync title already names the kind; a path title does
          not, unless the kind is the ordinary `file`. */}
      <FactsRow pairs={ev.path && ev.kind !== "file" ? [[t("journal.detail.kind"), ev.kind]] : []} />
      <div className="nums">
        <NumBox value={ev.files_indexed} label={t("journal.detail.filesIndexed")} />
        <NumBox value={ev.files_pruned} label={t("journal.detail.filesPruned")} />
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
                <div className="hit-p">
                  <FilePathLink bankId={ev.bank_id} path={ev.path} />
                </div>
                <div className="hit-h">{t("journal.detail.currentFileOf", { bank: bankName })}</div>
              </div>
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
        <p className="mnemo-empty-hint">{t("journal.detail.selectHint")}</p>
      </div>
    );
  }

  return (
    <div className="detail">
      {kind === "query" ? (
        <QueryDetail ev={event as QueryLogEvent} />
      ) : (
        <IndexDetail ev={event as IndexLogEvent} bankName={bankLabel(event.bank_id)} />
      )}
    </div>
  );
}
