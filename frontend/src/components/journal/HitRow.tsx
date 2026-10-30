"use client";

import { useT } from "@/lib/i18n/hooks";
import { HitSnapshot } from "./HitSnapshot";
import { FilePathLink } from "./FilePathLink";
import type { LogHit } from "@/lib/api/journal";

interface HitRowProps {
  hit: LogHit;
  index: number;
  bankId: string;
}

/** One ranked hit inside a query event's detail: rank, path (a link into
 *  Памʼять), heading, and the snapshot when the row carries one (see
 *  `journal.ts`'s `LogHit.content`). Raw RRF score and sim are not shown. */
export function HitRow({ hit, index, bankId }: HitRowProps) {
  const t = useT();

  return (
    <article className="hit">
      <div className="hit-top">
        <span className="hit-r">{index + 1}</span>
        <div className="hit-l">
          <div className="hit-p">
            <FilePathLink bankId={bankId} path={hit.path} />
          </div>
          <div className="hit-h">
            {t("journal.hit.chunkLabel", { heading: hit.heading || "—", n: hit.chunk_index })}
          </div>
        </div>
      </div>
      {hit.content && <HitSnapshot content={hit.content} />}
    </article>
  );
}
