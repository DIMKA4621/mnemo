"use client";

import { useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import {
  getBankMcpWiring,
  getBankToken,
  getBanks,
  getFile,
  getFsDirs,
  getStatus,
  getTree,
  type BankInfo,
} from "@/lib/api/memory";

export function useBanks() {
  return useQuery({
    queryKey: queryKeys.banks.all,
    queryFn: async () => (await getBanks()).banks,
  });
}

export interface BankCardData extends BankInfo {
  /** `status.queue.by_bank[bank.id].depth` — the live counter, not the
   *  fetch-time `BankInfo.queued` snapshot. Falls back to the REST field
   *  only while `/api/status` hasn't loaded yet. Never source a bank card's
   *  queued/indexing badge from `BankInfo.queued`/`indexing` directly. */
  displayQueued: number;
  displayIndexing: boolean;
}

/** `useBanks()` merged with `/api/status`'s live `queue.by_bank` counters. */
export function useBankCards() {
  const banksQuery = useBanks();
  const statusQuery = useStatus();
  const byBank = statusQuery.data?.queue.by_bank;

  const banks: BankCardData[] | undefined = banksQuery.data?.map((bank) => {
    const live = byBank?.[bank.id];
    return {
      ...bank,
      displayQueued: live?.depth ?? bank.queued,
      displayIndexing: live?.indexing ?? bank.indexing,
    };
  });

  return { ...banksQuery, data: banks };
}

export function useStatus() {
  return useQuery({
    queryKey: queryKeys.status.all,
    queryFn: getStatus,
    // The `queue` WS event patches this same cache entry directly (see
    // `lib/ws/dispatch.ts`) — this refetch interval is only the fallback for
    // when the socket is down or before it connects.
    refetchInterval: 15_000,
  });
}

export function useTree(bankId: string | null) {
  return useQuery({
    queryKey: queryKeys.tree.bank(bankId ?? ""),
    queryFn: () => getTree(bankId as string),
    enabled: !!bankId,
  });
}

export function useFile(bankId: string | null, path: string | null) {
  return useQuery({
    queryKey: queryKeys.file.one(bankId ?? "", path ?? ""),
    queryFn: () => getFile(bankId as string, path as string),
    enabled: !!bankId && !!path,
  });
}

export function useBankToken(bankId: string | null, open: boolean) {
  return useQuery({
    queryKey: queryKeys.bankToken.one(bankId ?? ""),
    queryFn: () => getBankToken(bankId as string),
    enabled: !!bankId && open,
  });
}

export function useBankMcpWiring(bankId: string | null, open: boolean) {
  return useQuery({
    queryKey: queryKeys.bankMcpWiring.one(bankId ?? ""),
    queryFn: () => getBankMcpWiring(bankId as string),
    enabled: !!bankId && open,
  });
}

export function useFsDirs(path: string | null, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.fsDirs.at(path),
    queryFn: () => getFsDirs(path),
    enabled,
    staleTime: 0,
  });
}
