"use client";

import { useTranslations } from "next-intl";

/**
 * Root-namespace translator: callers pass the full dotted key exactly like
 * the vanilla console's `t('common.gate.missing.title')` did — next-intl
 * resolves a dotted key against the nested message tree on its own, no
 * namespace pre-selection needed. Plural forms are ICU strings baked into
 * the JSON dictionaries at conversion time, so `t('memory.count.banks', {n:
 * 3})` is both `t()` and the vanilla `plural()` in one call; there is no
 * separate plural function to port.
 */
export function useT() {
  return useTranslations();
}

/**
 * Like `t()`, but returns `null` instead of throwing/warning when `key` is
 * absent — for callers with their own legitimate raw-text fallback (backend
 * preset text with no matching translation yet). Mirrors `tMaybe()` in the
 * vanilla console's `app.js`; next-intl has no built-in equivalent.
 */
export function useTMaybe() {
  const t = useTranslations();
  return function tMaybe(key: string, vars?: Record<string, string | number>): string | null {
    if (!t.has(key)) return null;
    return t(key, vars);
  };
}
