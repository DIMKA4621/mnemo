/**
 * Query key factory (contract-agnostic — Phase 1 infra only). Hierarchical
 * arrays so a broad invalidation (`invalidateQueries({ queryKey:
 * queryKeys.tree.all })`) matches every more-specific key nested under it,
 * the standard TanStack Query idiom. Filled in with real params as each
 * page's queries land in Phases 2-4; the shapes below are provisional.
 */

export type LogFilters = {
  bank?: string;
  since?: string;
  until?: string;
  kind?: "query" | "index";
};

export const queryKeys = {
  banks: {
    all: ["banks"] as const,
  },
  bankToken: {
    one: (bankId: string) => ["bankToken", bankId] as const,
  },
  bankMcpWiring: {
    one: (bankId: string) => ["bankMcpWiring", bankId] as const,
  },
  fsDirs: {
    at: (path: string | null) => ["fsDirs", path ?? null] as const,
  },
  tree: {
    all: ["tree"] as const,
    bank: (bankId: string) => ["tree", bankId] as const,
  },
  file: {
    all: ["file"] as const,
    one: (bankId: string, path: string) => ["file", bankId, path] as const,
  },
  search: {
    all: ["search"] as const,
    query: (bankId: string, query: string, pathPrefix?: string) =>
      ["search", bankId, query, pathPrefix ?? null] as const,
  },
  logs: {
    all: ["logs"] as const,
    list: (filters: LogFilters = {}) => ["logs", filters] as const,
  },
  status: {
    all: ["status"] as const,
  },
  settings: {
    all: ["settings"] as const,
  },
  embedState: {
    all: ["embedState"] as const,
  },
  updateStatus: {
    all: ["updateStatus"] as const,
  },
  doctor: {
    all: ["doctor"] as const,
  },
  autostart: {
    all: ["autostart"] as const,
  },
  catalog: {
    all: ["catalog"] as const,
    list: (category?: string) => ["catalog", category ?? null] as const,
    one: (entryId: string) => ["catalog", "entry", entryId] as const,
  },
  agents: {
    all: ["agents"] as const,
    one: (slug: string) => ["agents", slug] as const,
  },
  agentLaunch: {
    one: (slug: string) => ["agentLaunch", slug] as const,
  },
};
