import { getRequestConfig } from "next-intl/server";
import en from "@/lib/i18n/messages/en.json";

/**
 * Required by next-intl even without `[locale]` routing/middleware: the
 * static-export build still does one server-side prerender pass for every
 * "use client" component (`next build`'s "Generating static pages" step),
 * and without this file that pass throws `ENVIRONMENT_FALLBACK` — there is
 * no request-scoped locale to resolve, no middleware ever runs for a
 * static export, and `NextIntlClientProvider` alone only covers the
 * client-side render.
 *
 * Always resolves to English: this is the deterministic default the
 * prerendered HTML shows before hydration, matching every other
 * client-only preference in this app (`lib/store/ui.ts`'s `hydrate()`
 * pattern) — the real preferred locale takes over once `Providers.tsx`
 * mounts and reads `localStorage`.
 *
 * `timeZone` is required too: without one, next-intl throws
 * `ENVIRONMENT_FALLBACK` during this same server prerender pass (a
 * `timeZone`-dependent format could otherwise differ between the
 * build-time server and the visitor's own browser, causing a hydration
 * mismatch). `UTC` is the deterministic build-time default for the same
 * reason `en`/dark are — no date formatting exists yet in Phase 1's shell.
 */
export default getRequestConfig(async () => {
  return {
    locale: "en",
    messages: en,
    timeZone: "UTC",
  };
});
