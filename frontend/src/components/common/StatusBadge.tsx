"use client";

export type StatusBadgeVariant = "ready" | "indexing" | "empty" | "git" | "nogit" | "frozen" | "off";

/**
 * Pill status badge — `.mnemo-badge`/`.mnemo-badge-*` (`app/tokens.css`),
 * already ported 1:1 from the vanilla console's badge palette. This
 * component only picks the variant class; the tokens carry the color.
 */
export function StatusBadge({
  variant,
  text,
  title,
}: {
  variant: StatusBadgeVariant;
  text: string;
  title?: string;
}) {
  return (
    <span className={`mnemo-badge mnemo-badge-${variant}`} title={title}>
      {text}
    </span>
  );
}
