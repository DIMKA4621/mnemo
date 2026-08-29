import type { CatalogCategory, CatalogEntry } from "@/lib/api/catalog";

/** Same shape as `catalog.py`'s `_VAR_RE` (`src/catalog.py`) — kept
 *  identical so a config that shows N chips here parses to the same N names
 *  server-side. */
const VAR_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

export function parseVars(text: string): string[] {
  const seen = new Set<string>();
  const found: string[] = [];
  let m: RegExpExecArray | null;
  VAR_RE.lastIndex = 0;
  while ((m = VAR_RE.exec(text || "")) !== null) {
    if (!seen.has(m[1])) {
      seen.add(m[1]);
      found.push(m[1]);
    }
  }
  return found;
}

/** JSON kind + static/template summary for an `mcp` entry, or a truncated
 *  first line for a `skill`/`rule`'s free text — the list row's meta line
 *  and the "Додати" modal's dedup preview both want this. */
export function catalogEntryMeta(entry: Pick<CatalogEntry, "category" | "content" | "vars">): string {
  if (entry.category === "mcp") {
    let kind = "?";
    try {
      const parsed = JSON.parse(entry.content);
      if (parsed && typeof parsed.type === "string") {
        kind = parsed.type === "http" ? "HTTP" : parsed.type;
      }
    } catch {
      // Invalid JSON never reaches a saved entry — validated on write.
    }
    return entry.vars.length ? `${kind} · template, ${entry.vars.length}` : `${kind} · static`;
  }
  const text = (entry.content || "").trim().replace(/\s+/g, " ");
  if (!text) return "";
  return text.length > 70 ? `${text.slice(0, 67)}…` : text;
}

export function contentLabelKey(category: CatalogCategory): string {
  return category === "mcp" ? "registry.detail.contentLabelMcp" : "registry.detail.contentLabelText";
}

/** Recursively sorts object keys before stringifying — same comparison rule
 *  as the backend's `catalog.canonical_json` (`json.dumps(parsed,
 *  sort_keys=True)`), so a config that differs from another only in key
 *  order is caught client-side too, not just on save. Returns `null` for
 *  anything that fails to parse as JSON. */
export function canonicalJson(text: string): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  return JSON.stringify(sortKeysDeep(parsed));
}

function sortKeysDeep(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeysDeep);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      out[key] = sortKeysDeep((value as Record<string, unknown>)[key]);
    }
    return out;
  }
  return value;
}

/** The existing `mcp` entry in `entries` whose canonicalised config matches
 *  `content`'s, or `null` — mirrors `catalog.py`'s `_find_duplicate_mcp`. */
export function findDuplicateMcp(
  entries: CatalogEntry[],
  content: string,
  excludeId: string | null,
): CatalogEntry | null {
  const normalized = canonicalJson(content);
  if (normalized === null) return null;
  return (
    entries.find(
      (e) => e.category === "mcp" && e.id !== excludeId && canonicalJson(e.content) === normalized,
    ) ?? null
  );
}
