"use client";

import Link from "next/link";
import { Button } from "antd";
import { useT } from "@/lib/i18n/hooks";
import { HitSnapshot } from "./HitSnapshot";
import type { LogHit } from "@/lib/api/journal";

function fmtScore(v: number | null | undefined): string {
  return v == null ? "—" : v.toFixed(4);
}

interface HitRowProps {
  hit: LogHit;
  index: number;
  bankId: string;
}

/** One ranked hit inside a query event's detail — score/sim, the snapshot
 *  (when the row carries one, see `journal.ts`'s `LogHit.content`), and a
 *  real cross-page `<Link>` into Памʼять (no single-page tab switching any
 *  more, every page is its own route). */
export function HitRow({ hit, index, bankId }: HitRowProps) {
  const t = useT();
  const href = `/memory?bank=${encodeURIComponent(bankId)}&path=${encodeURIComponent(hit.path)}`;

  return (
    <article className="hit">
      <div className="hit-top">
        <span className="hit-r">{index + 1}</span>
        <div className="hit-l">
          <div className="hit-p">{hit.path}</div>
          <div className="hit-h">
            {t("journal.hit.chunkLabel", { heading: hit.heading || "—", n: hit.chunk_index })}
          </div>
        </div>
        <span className="hit-s">
          score {fmtScore(hit.score)}
          <br />
          sim {fmtScore(hit.sim)}
        </span>
      </div>
      {hit.content && <HitSnapshot content={hit.content} />}
      <div className="hit-foot">
        <Link href={href}>
          <Button size="small">{t("journal.hit.openFile")}</Button>
        </Link>
      </div>
    </article>
  );
}
