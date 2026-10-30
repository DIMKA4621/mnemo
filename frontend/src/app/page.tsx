"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * `next.config.js`'s config-level `redirects()` is unsupported under
 * `output: 'export'` (`docs/02-guides/static-exports.md`'s Unsupported
 * Features list) — a plain client-side navigation on mount is the
 * static-export-safe equivalent, and this is the only route that needs it.
 */
export default function RootPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/memory");
  }, [router]);
  return null;
}
