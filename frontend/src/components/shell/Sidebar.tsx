"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUiStore } from "@/lib/store/ui";
import { useT } from "@/lib/i18n/hooks";
import {
  AgentsIcon,
  CollapseIcon,
  JournalIcon,
  MemoryIcon,
  RegistryIcon,
  SettingsIcon,
} from "@/components/common/icons";
import { useStatus } from "@/hooks/useMemoryQueries";
import { UpdateBanner } from "@/components/settings/UpdateBanner";
import { WsStatusIndicator } from "./WsStatusIndicator";

// `Agents-design.md` §5 "Чинна структура" (revised 2026-08-25): Memory /
// Agents / Registry / Journal / Settings.
const NAV_ITEMS = [
  { href: "/memory", key: "shell.nav.memory", Icon: MemoryIcon },
  { href: "/agents", key: "shell.nav.agents", Icon: AgentsIcon },
  { href: "/registry", key: "shell.nav.registry", Icon: RegistryIcon },
  { href: "/journal", key: "shell.nav.journal", Icon: JournalIcon },
  { href: "/settings", key: "shell.nav.settings", Icon: SettingsIcon },
] as const;

export function Sidebar() {
  const t = useT();
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const pathname = usePathname();

  return (
    <aside className="mnemo-sidebar">
      <div className="sb-top">
        <span className="sb-brand">mnemo</span>
        <button
          className="sb-toggle"
          type="button"
          aria-label={t(collapsed ? "shell.sidebar.expand" : "shell.sidebar.collapse")}
          title={t(collapsed ? "shell.sidebar.expand" : "shell.sidebar.collapse")}
          onClick={toggleSidebar}
        >
          <CollapseIcon />
        </button>
      </div>

      <nav className="sb-nav" aria-label={t("shell.nav.ariaLabel")}>
        {NAV_ITEMS.map(({ href, key, Icon }) => {
          const active = pathname === href || pathname?.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={`sb-item${active ? " is-active" : ""}`}
              title={t(key)}
              // Next 16's static-export RSC-payload prefetch requests a
              // path (`__next.<segment>.__PAGE__.txt`) that does not match
              // what `next build --output export` actually writes to disk
              // (`__next.<segment>/__PAGE__.txt`, a nested path) — a real
              // 404 on every hover, not something this app's own code
              // causes. Disabling prefetch on the four permanent nav links
              // sidesteps it; client-side navigation itself is unaffected
              // (confirmed live), only the eager-fetch-on-hover optimization
              // is lost, which barely matters for a loopback console.
              prefetch={false}
            >
              <Icon />
              <span className="lbl">{t(key)}</span>
            </Link>
          );
        })}
      </nav>

      <SidebarFooter />
    </aside>
  );
}

/** A local build's tag carries its base release plus a lowercase "l" marker
 *  (`v3.0.1l`) — see `UpdateBanner.tsx`'s `baseVersionTag` for the same
 *  scheme applied to a comparison instead of a display class. */
function isLocalBuildTag(tag: string | null | undefined): boolean {
  return !!tag && /\dl$/.test(tag);
}

/**
 * The machine's own facts (connection state, provider, version) — same spot
 * the vanilla console used (`shell.js`'s `sb-foot`), now wired to the real
 * `GET /api/status` cache `useStatus()` already polls every 15s for the
 * Memory page, so this costs no extra request. The self-update banner
 * mounts here too — it is a footer-level fact ("something needs your
 * attention about this machine"), not tied to the Settings page.
 */
function SidebarFooter() {
  const statusQuery = useStatus();
  const svc = statusQuery.data?.service;

  return (
    <div className="sb-foot" id="sb-foot">
      <div className="sb-foot-row">
        <WsStatusIndicator />
        <span className="sep">·</span>
        <span className="txt">{svc?.provider ?? "—"}</span>
        <span className={`ver${isLocalBuildTag(svc?.version) ? " is-local-build" : ""}`}>
          {svc?.version ?? "—"}
        </span>
      </div>
      <UpdateBanner />
    </div>
  );
}
