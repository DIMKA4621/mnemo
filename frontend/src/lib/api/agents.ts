import { api } from "./fetcher";

/** The agent registry (MN-40, `src/agent_registry.py`) — a folder whose
 *  `memory/` is registered as an ordinary bank, plus a `launch.json` that
 *  decides which Claude Code backend it runs against. */

/** `read_launch_config()`'s two shapes (`agent_registry.py`'s
 *  `validate_launch_config`) — `standard` takes no other fields, `custom`
 *  requires `host`/`port`. Wire values stay `standard`/`custom` (the
 *  already-merged backend's naming, MN-42 lead decision 29.08.2026) — only
 *  the UI labels say "Системна (Claude Code)"/"Проксі". */
export type LaunchConfig =
  | { mode: "standard" }
  | {
      mode: "custom";
      host: string;
      port: number;
      model?: string;
      autocompact?: boolean;
      extra_args?: string[];
    };

/** The one agent shape `_agent_info()` returns (`src/api.py`). `launch` can
 *  also come back as `{ error: string }` — a hand-edited `launch.json` that
 *  fails validation must not break the whole listing, so the backend reports
 *  it inline instead of failing the request. */
export interface AgentInfo {
  slug: string;
  name: string;
  root: string;
  owns_root: boolean;
  created_at: string;
  bank_id: string;
  bank_name: string | null;
  launch: LaunchConfig | { error: string };
}

/** `agent_registry.preview_adopt()`'s response — a read-only, zero-side-effect
 *  inspection of a candidate folder, called before `POST /api/agents` so the
 *  wizard can show an adoption-confirmation dialog for a non-empty folder. */
export interface AgentPreview {
  root_exists: boolean;
  empty: boolean;
  already_registered_agent: string | null;
  has_claude_md: boolean;
  claude_md_excerpt: string | null;
  has_mcp_json: boolean;
  mcp_server_names: string[];
  has_claude_dir: boolean;
  rule_files: string[];
  skill_dirs: string[];
  has_memory: boolean;
  memory_already_bank: boolean;
  suggested_slug: string;
  suggested_name: string;
}

export interface AgentPreviewRequest {
  root: string;
}

export interface CreateAgentRequest {
  name: string;
  root?: string | null;
  claude_md?: string | null;
  /** Adopting a non-empty folder without this set gets a 409
   *  (`adoption_confirmation_required`) carrying the same preview the
   *  wizard already showed before asking. */
  confirm_adopt?: boolean;
}

export function getAgents(): Promise<{ agents: AgentInfo[] }> {
  return api("/api/agents");
}

export function previewAgent(req: AgentPreviewRequest): Promise<AgentPreview> {
  return api("/api/agents/preview", { method: "POST", body: req });
}

export function createAgent(req: CreateAgentRequest): Promise<AgentInfo> {
  return api("/api/agents", { method: "POST", body: req });
}

export function getAgent(slug: string): Promise<AgentInfo> {
  return api(`/api/agents/${encodeURIComponent(slug)}`);
}

export function deleteAgent(slug: string): Promise<{ ok: boolean }> {
  return api(`/api/agents/${encodeURIComponent(slug)}`, { method: "DELETE" });
}

export function getAgentLaunch(slug: string): Promise<LaunchConfig> {
  return api(`/api/agents/${encodeURIComponent(slug)}/launch`);
}

export function putAgentLaunch(slug: string, config: LaunchConfig): Promise<LaunchConfig> {
  return api(`/api/agents/${encodeURIComponent(slug)}/launch`, { method: "PUT", body: config });
}
