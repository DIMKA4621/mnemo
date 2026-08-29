"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query/keys";
import {
  applyUpdate,
  cancelAutoPending,
  checkForUpdate,
  cleanOrphans,
  confirmAutoPending,
  downloadEmbedModel,
  loadEmbed,
  putSettings,
  setAutostart,
  unloadEmbed,
  type SettingsPutPayload,
} from "@/lib/api/settings";

export function usePutSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: SettingsPutPayload) => putSettings(payload),
    onSuccess: (data) => qc.setQueryData(queryKeys.settings.all, data),
  });
}

export function useSetAutostart() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) => setAutostart(enabled),
    onSuccess: (data) => qc.setQueryData(queryKeys.autostart.all, data),
  });
}

export function useDownloadEmbedModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => downloadEmbedModel(),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.embedState.all }),
  });
}

export function useUnloadEmbed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unloadEmbed(),
    onSuccess: (data) => qc.setQueryData(queryKeys.embedState.all, data),
  });
}

export function useLoadEmbed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => loadEmbed(),
    onSuccess: (data) => qc.setQueryData(queryKeys.embedState.all, data),
  });
}

export function useCleanOrphans() {
  return useMutation({
    mutationFn: (ids: string[]) => cleanOrphans(ids),
  });
}

export function useCheckForUpdate() {
  return useMutation({
    mutationFn: () => checkForUpdate(),
  });
}

export function useApplyUpdate() {
  return useMutation({
    mutationFn: (tag: string) => applyUpdate(tag),
  });
}

export function useConfirmAutoPending() {
  return useMutation({
    mutationFn: () => confirmAutoPending(),
  });
}

export function useCancelAutoPending() {
  return useMutation({
    mutationFn: () => cancelAutoPending(),
  });
}
