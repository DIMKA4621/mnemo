"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import {
  createCatalogEntry,
  deleteCatalogEntry,
  updateCatalogEntry,
  type CreateCatalogEntryRequest,
  type UpdateCatalogEntryRequest,
} from "@/lib/api/catalog";

export function useCreateCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateCatalogEntryRequest) => createCatalogEntry(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all }),
  });
}

export function useUpdateCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ entryId, patch }: { entryId: string; patch: UpdateCatalogEntryRequest }) =>
      updateCatalogEntry(entryId, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all }),
  });
}

export function useDeleteCatalogEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entryId: string) => deleteCatalogEntry(entryId),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.catalog.all }),
  });
}
