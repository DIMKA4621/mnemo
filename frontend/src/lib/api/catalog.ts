import { api } from "./fetcher";

/** The general MCP/Skills/Rules registry (MN-41/MN-42, `src/catalog.py`) —
 *  agent-agnostic, one flat store of reusable entries a human adds by hand. */
export type CatalogCategory = "mcp" | "skill" | "rule";

/** The one catalog-entry shape `_entry_info()` returns (`src/api.py`). */
export interface CatalogEntry {
  id: string;
  category: CatalogCategory;
  name: string;
  content: string;
  created_at: string;
  /** `{{VAR}}` placeholder names found in `content` — `mcp` entries only,
   *  always recomputed server-side from `content`, never client-supplied. */
  vars: string[];
  /** How many agents reference this entry via `links.json` (`src/agent_registry.py`
   *  `catalog_entry_used_by`, MN-48). Always server-computed. */
  used_by_count: number;
}

export interface CreateCatalogEntryRequest {
  category: CatalogCategory;
  name: string;
  content: string;
}

/** `category` is absent on purpose — fixed at creation, same as the backend's
 *  `UpdateCatalogEntryRequest`. */
export interface UpdateCatalogEntryRequest {
  name?: string;
  content?: string;
}

export function getCatalog(category?: CatalogCategory): Promise<{ entries: CatalogEntry[] }> {
  const q = category ? `?category=${encodeURIComponent(category)}` : "";
  return api(`/api/catalog${q}`);
}

export function getCatalogEntry(entryId: string): Promise<CatalogEntry> {
  return api(`/api/catalog/${encodeURIComponent(entryId)}`);
}

export function createCatalogEntry(req: CreateCatalogEntryRequest): Promise<CatalogEntry> {
  return api("/api/catalog", { method: "POST", body: req });
}

export function updateCatalogEntry(entryId: string, patch: UpdateCatalogEntryRequest): Promise<CatalogEntry> {
  return api(`/api/catalog/${encodeURIComponent(entryId)}`, { method: "PATCH", body: patch });
}

export function deleteCatalogEntry(entryId: string): Promise<{ ok: boolean }> {
  return api(`/api/catalog/${encodeURIComponent(entryId)}`, { method: "DELETE" });
}
