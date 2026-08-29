import en from "./messages/en.json";
import uk from "./messages/uk.json";
import { deepMerge } from "./deep-merge";
import type { Lang } from "../store/ui";

// English-plus-Ukrainian-overrides — see `deep-merge.ts`'s docstring. Both
// exported statically (no per-locale dynamic import): the whole dictionary
// is ~650 lines/two locales, small enough to bundle both rather than add a
// loading state for a language switch that used to be instant.
const merged: Record<Lang, typeof en> = {
  en,
  uk: deepMerge(en, uk),
};

export function messagesFor(lang: Lang) {
  return merged[lang];
}
