"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import {
  addBank,
  patchBank,
  regenerateBankToken,
  reindex,
  removeBank,
  type AddBankRequest,
  type PatchBankRequest,
} from "@/lib/api/memory";

export function useAddBank() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: AddBankRequest) => addBank(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.banks.all }),
  });
}

export function usePatchBank() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId, req }: { bankId: string; req: PatchBankRequest }) => patchBank(bankId, req),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.banks.all }),
  });
}

export function useRemoveBank() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId, dropIndex, stripMcp }: { bankId: string; dropIndex: boolean; stripMcp: boolean }) =>
      removeBank(bankId, { dropIndex, stripMcp }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.banks.all }),
  });
}

export function useRegenerateToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bankId: string) => regenerateBankToken(bankId),
    onSuccess: (_data, bankId) =>
      qc.invalidateQueries({ queryKey: queryKeys.bankToken.one(bankId) }),
  });
}

export function useReindex() {
  return useMutation({
    mutationFn: (req: { bank: string; path?: string; full?: boolean }) => reindex(req),
  });
}
