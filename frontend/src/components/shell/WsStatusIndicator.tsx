"use client";

import { useWsStatusStore } from "@/lib/store/ws-status";
import { useT } from "@/lib/i18n/hooks";

const KEY_BY_KIND: Record<string, string> = {
  open: "shell.conn.live",
  connecting: "shell.conn.connecting",
  error: "shell.conn.dropped",
  idle: "common.gate.idle",
};

/**
 * Lives in the sidebar footer (`Sidebar.tsx`'s `SidebarFooter`), not the
 * topbar — same spot the vanilla console used for its "machine facts" row
 * (`shell.js`'s `renderService()`/`setConnState()`: dot + status text,
 * `·`-separated from provider/version). Moved here 2026-08-29 per the
 * user's decision: the topbar carries only language + theme, nothing about
 * connection state.
 *
 * Renders the same `.dot`/`.txt` markup `sb-foot` already uses, so it
 * drops straight into that row rather than bringing its own layout.
 */
export function WsStatusIndicator() {
  const kind = useWsStatusStore((s) => s.kind);
  const t = useT();
  const cls =
    kind === "open" ? "dot" : kind === "error" ? "dot err" : kind === "connecting" ? "dot busy" : "dot idle";

  return (
    <>
      <i className={cls} />
      <span className="txt">{t(KEY_BY_KIND[kind] ?? "common.gate.idle")}</span>
    </>
  );
}
