import { api } from "./fetcher";
import type { McpMode } from "@/lib/memory/tokenSnippets";

type McpServers = { mcpServers: Record<string, { type: string; url: string }> };

/** `GET /api/admin-mcp` — the service token and the ready-made snippets for `/mcp-admin`. */
export interface AdminMcpInfo {
  token: string;
  url: string;
  host: string;
  port: number;
  entry_name: string;
  snippet: {
    literal: McpServers;
    template: {
      template_entry: McpServers;
      env_var: string;
      env_line: string;
      env_block: string;
      sed_line: string;
    };
  };
}

export type AdminWiringAction = "create" | "add" | "already" | "refused";
export type AdminWiringReason =
  | "present"
  | "not_found"
  | "not_a_directory"
  | "invalid_json"
  | "tracked"
  | "index_unreadable"
  | "needs_migrate";

/** `GET /api/admin-mcp/wiring` — what wiring would do in one folder; writes nothing. */
export interface AdminWiringPreview {
  project_root: string;
  mode: McpMode;
  suggested_mode: McpMode;
  exists: boolean;
  mcp_json_exists: boolean;
  uses_template: boolean;
  target: string;
  has_entry: boolean;
  entry_current: boolean;
  tracked: boolean;
  tracked_files: string[];
  writes: string[];
  warnings: string[];
  notes: string[];
  action: AdminWiringAction;
  reason: AdminWiringReason | null;
}

export interface AdminWiringResult {
  written: string[];
  log: string[];
  notes: string[];
  regenerated: { script: string; ok: boolean; output: string[] } | null;
  preview: AdminWiringPreview;
}

export function getAdminMcp(): Promise<AdminMcpInfo> {
  return api("/api/admin-mcp");
}

export function getAdminWiring(root: string, mode: McpMode): Promise<AdminWiringPreview> {
  const qs = new URLSearchParams({ root, mode });
  return api(`/api/admin-mcp/wiring?${qs.toString()}`);
}

export function wireAdminMcp(root: string, mode: McpMode): Promise<AdminWiringResult> {
  return api("/api/admin-mcp/wiring", { method: "POST", body: { root, mode } });
}
