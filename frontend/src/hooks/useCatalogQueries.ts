"use client";

import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import { getCatalog, getCatalogEntry, type CatalogCategory } from "@/lib/api/catalog";

export function useCatalog(category: CatalogCategory) {
  return useQuery({
    queryKey: queryKeys.catalog.list(category),
    queryFn: async () => (await getCatalog(category)).entries,
  });
}

export function useCatalogEntry(entryId: string | null) {
  return useQuery({
    queryKey: queryKeys.catalog.one(entryId ?? ""),
    queryFn: () => getCatalogEntry(entryId as string),
    enabled: !!entryId,
  });
}
