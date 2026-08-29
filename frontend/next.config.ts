import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

// `next build` always runs with NODE_ENV=production, `next dev` with
// NODE_ENV=development — no separate flag needed to tell the two apart.
const isDev = process.env.NODE_ENV !== "production";

// The real running service defaults to :4646; point this at
// `src/webui/devserver.py` (:8919, the stdlib-only fixture mock) instead
// when iterating without a live backend:
//   MNEMO_BACKEND=http://127.0.0.1:8919 npm run dev
const backend = process.env.MNEMO_BACKEND || "http://127.0.0.1:4646";

const nextConfig: NextConfig = {
  // `output: 'export'` disallows `rewrites()` outright — next dev refuses to
  // start with both set at once (see docs/02-guides/static-exports.md's
  // Unsupported Features list). Static export only matters for `next build`
  // (the committed output that ships to `src/webui/static/`); `next dev`
  // never runs `output: 'export'`, so the two never actually conflict.
  ...(isDev ? {} : { output: "export" as const }),
  basePath: "/ui",
  trailingSlash: true,
  images: {
    unoptimized: true,
  },
  // Inlined into the client bundle so the WS client (browser-side, cannot
  // read plain env vars) can connect straight to the dev backend's own `/ws`
  // instead of going through this rewrite proxy — see
  // `src/lib/ws/client.ts` for why: Next's rewrite layer forwards plain HTTP
  // but does not reliably forward a WebSocket Upgrade handshake, so `/api/*`
  // goes through the proxy below while `/ws` is dialed directly.
  env: {
    NEXT_PUBLIC_MNEMO_BACKEND: isDev ? backend : "",
  },
  async rewrites() {
    if (!isDev) return [];
    return [
      {
        // Root-level, deliberately outside `basePath`: the production
        // build's client code calls plain `fetch('/api/...')` (an absolute
        // path, not `next/link`/`next/router`), because the real backend
        // mounts `/api/*` at the service root, not under `/ui`. Without
        // `basePath: false` here, Next would only match `/ui/api/*` and this
        // rewrite would silently never fire in dev.
        source: "/api/:path*",
        basePath: false,
        destination: `${backend}/api/:path*`,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
