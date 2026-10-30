"use client";

import { useUiStore } from "@/lib/store/ui";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { GateOverlay } from "./GateOverlay";
import { ErrorBanner } from "./ErrorBanner";
import { UpdateModal } from "@/components/settings/UpdateModal";
import "./shell.css";

export function AppShell({ children }: { children: React.ReactNode }) {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);

  return (
    <div className={`mnemo-app${collapsed ? " is-collapsed" : ""}`}>
      <Sidebar />
      <div className="mnemo-stage">
        <Topbar />
        <ErrorBanner />
        <main className="mnemo-main">{children}</main>
      </div>
      <GateOverlay />
      <UpdateModal />
    </div>
  );
}
