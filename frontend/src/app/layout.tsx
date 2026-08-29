import type { Metadata } from "next";
import "./tokens.css";
import "./globals.css";
import { Providers } from "./providers";
import { AppShell } from "@/components/shell/AppShell";

export const metadata: Metadata = {
  title: "mnemo",
  icons: {
    // Inline so the browser stops probing /favicon.ico at the origin root,
    // which the API does not serve and which logs a 404 on every load —
    // same reasoning and same icon as `src/webui/static/index.html`.
    icon:
      "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%236aa8ff'/%3E%3Crect x='3' y='4' width='10' height='1.6' fill='%2314161a'/%3E%3Crect x='3' y='7.2' width='10' height='1.6' fill='%2314161a'/%3E%3Crect x='3' y='10.4' width='6' height='1.6' fill='%2314161a'/%3E%3C/svg%3E",
  },
};

// Must run before any styled paint: dark is the CSS default, so a saved
// "light" preference needs `data-theme` set before first paint or every
// load flashes dark for a frame. Ported verbatim from
// `src/webui/static/index.html`'s own inline bootstrap script — this is
// also what lets `lib/theme/antd-theme.ts` route AntD's colors through the
// same CSS custom properties instead of a second, React-state-driven theme
// switch (see that file's docstring).
const THEME_BOOTSTRAP = `
if (localStorage.getItem('mnemo_theme') === 'light') {
  document.documentElement.dataset.theme = 'light';
}
`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // `suppressHydrationWarning` scoped to this one element only: the
    // bootstrap script above deliberately sets `data-theme` on `<html>`
    // before React hydrates, which React would otherwise (correctly) flag
    // as a server/client attribute mismatch. This is the documented
    // Next.js pattern for exactly this early-theme-script case — it does
    // not silence hydration warnings for the tree below.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
