"use client";

import Link from "next/link";
import { ArrowRightOutlined } from "@ant-design/icons";
import { useT } from "@/lib/i18n/hooks";

/** A file path that opens the file in Памʼять (`?bank=&path=`, applied once
 *  on that page's mount). */
export function FilePathLink({ bankId, path }: { bankId: string; path: string }) {
  const t = useT();
  const href = `/memory?bank=${encodeURIComponent(bankId)}&path=${encodeURIComponent(path)}`;

  return (
    <Link href={href} className="hit-link" title={t("journal.hit.openFile")}>
      {path}
      <ArrowRightOutlined className="hit-link-arrow" aria-hidden />
    </Link>
  );
}
