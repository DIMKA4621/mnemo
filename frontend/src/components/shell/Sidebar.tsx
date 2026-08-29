"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useUiStore } from "@/lib/store/ui";
import { useT } from "@/lib/i18n/hooks";
import { AgentsIcon, CollapseIcon, JournalIcon, MemoryIcon, SettingsIcon } from "@/components/common/icons";
import { WsStatusIndicator } from "./WsStatusIndicator";

const NAV_ITEMS = [
  { href: "/memory", key: "shell.nav.memory", Icon: MemoryIcon },
  { href: "/journal", key: "shell.nav.journal", Icon: JournalIcon },
  { href: "/settings", key: "shell.nav.settings", Icon: SettingsIcon },
  { href: "/agents", key: "shell.nav.agents", Icon: AgentsIcon },
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

/**
 * The machine's own facts (connection state, provider, version) — same spot
 * the vanilla console used (`shell.js`'s `sb-foot`). Provider/version are
 * not wired to `GET /api/status` yet (that lands with the Memory/Settings
 * pages in Phases 2-4); the WS connection dot+label is real in Phase 1, so
 * the shell's own infra is visibly working end to end. The topbar carries
 * only language + theme (user decision, 2026-08-29) — connection state
 * lives here, not duplicated there.
 */
function SidebarFooter() {
  return (
    <div className="sb-foot" id="sb-foot">
      <WsStatusIndicator />
      <span className="sep">·</span>
      <span className="txt">—</span>
      <span className="ver">—</span>
    </div>
  );
}
