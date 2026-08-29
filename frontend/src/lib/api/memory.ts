import { api } from "./fetcher";

/** Contract §9.5's one bank shape (`_bank_info` in `src/api.py`). */
export interface BankInfo {
  id: string;
  name: string;
  root: string;
  provider: string | null;
  state: "enabled" | "frozen" | "disabled";
  enabled: boolean;
  exists: boolean;
  git: boolean;
  files: number;
  chunks: number;
  db_bytes: number;
  last_indexed: string | null;
  status: "ready" | "indexing" | "empty";
  queued: number;
  indexing: boolean;
  last_error: string | null;
  provider_active: string | null;
  provider_key: string | null;
  index_provider_key: string | null;
  rebuild_pending: boolean;
  provider_error: string | null;
  /** Only present on the `POST /api/banks` response when `init: true` was sent. */
  init?: { ok: boolean; log?: string[]; skipped?: boolean; reason?: string };
}

export interface AddBankRequest {
  root: string;
  name?: string | null;
  provider?: string | null;
  init?: boolean;
  create_structure?: boolean;
}

export interface PatchBankRequest {
  state?: BankInfo["state"];
  name?: string;
  provider?: string;
}

export interface RemoveBankResult {
  ok: boolean;
  index_removed: boolean;
  mcp_stripped?: string[];
}

export interface BankTokenInfo {
  bank_id: string;
  name: string;
  token: string;
}

export interface BankMcpWiring {
  has_wiring: boolean;
  uses_template: boolean;
  project_root: string | null;
}

export interface FsDirEntry {
  name: string;
  path: string;
  registered: string | null;
}

export interface FsRoot {
  name: string;
  path: string;
}

export interface FsDirsResult {
  path: string;
  display: string;
  parent: string | null;
  home: string;
  roots: FsRoot[];
  registered: string | null;
  md: number;
  md_capped: boolean;
  entries: FsDirEntry[];
  truncated: boolean;
  memory_dir: string;
  has_claude_memory: boolean;
}

export interface ReindexResult {
  ok: boolean;
  task_ids: (string | number)[];
  queued: number;
}

export interface TreeNode {
  name: string;
  type: "dir" | "file";
  path: string;
  children?: TreeNode[];
  size?: number;
  indexed?: boolean;
  chunks?: number;
  headings?: string[];
}

export interface TreeResult {
  bank_id: string;
  root: string;
  files: number;
  dirs: number;
  tree: TreeNode;
  pending: string[];
}

export interface ChunkInfo {
  chunk_uid: string;
  chunk_index: number;
  heading: string | null;
  start_char: number;
  end_char: number;
}

export interface FileResult {
  bank_id: string;
  path: string;
  size: number;
  sha256: string;
  indexed: boolean;
  text: string;
  chunks: ChunkInfo[];
}

export interface QueueByBankEntry {
  depth: number;
  indexing: boolean;
}

export interface StatusResult {
  service: {
    version: string;
    pid: number;
    host: string;
    port: number;
    started_at: string;
    uptime_s: number;
    provider: string | null;
    provider_model: string | null;
    provider_dim: number | null;
    provider_key: string | null;
    provider_error: string | null;
    priority_enabled: boolean;
    embed: Record<string, unknown>;
  };
  queue: {
    depth: number;
    high: number;
    normal: number;
    low: number;
    current: {
      task_id: string | number;
      bank_id: string | null;
      kind: string;
      path: string | null;
      batch: number;
      batches: number;
      started_at: number;
    } | null;
    by_bank: Record<string, QueueByBankEntry>;
  };
  banks: BankInfo[];
}

export function getBanks(): Promise<{ banks: BankInfo[] }> {
  return api("/api/banks");
}

export function addBank(req: AddBankRequest): Promise<BankInfo> {
  return api("/api/banks", { method: "POST", body: req });
}

export function patchBank(bankId: string, req: PatchBankRequest): Promise<BankInfo> {
  return api(`/api/banks/${encodeURIComponent(bankId)}`, { method: "PATCH", body: req });
}

export function removeBank(
  bankId: string,
  opts: { dropIndex: boolean; stripMcp: boolean },
): Promise<RemoveBankResult> {
  const params = new URLSearchParams({
    drop_index: String(opts.dropIndex),
    strip_mcp: String(opts.stripMcp),
  });
  return api(`/api/banks/${encodeURIComponent(bankId)}?${params.toString()}`, { method: "DELETE" });
}

export function getBankToken(bankId: string): Promise<BankTokenInfo> {
  return api(`/api/banks/${encodeURIComponent(bankId)}/token`);
}

export function regenerateBankToken(bankId: string): Promise<BankTokenInfo> {
  return api(`/api/banks/${encodeURIComponent(bankId)}/token`, { method: "POST" });
}

export function getBankMcpWiring(bankId: string): Promise<BankMcpWiring> {
  return api(`/api/banks/${encodeURIComponent(bankId)}/mcp-wiring`);
}

export function getFsDirs(path: string | null): Promise<FsDirsResult> {
  const q = path ? `?path=${encodeURIComponent(path)}` : "";
  return api(`/api/fs/dirs${q}`);
}

export function reindex(req: { bank: string; path?: string; full?: boolean }): Promise<ReindexResult> {
  return api("/api/reindex", { method: "POST", body: req });
}

export function getTree(bankId: string): Promise<TreeResult> {
  const q = new URLSearchParams({ bank: bankId, links: "false", depth: "0" });
  return api(`/api/tree?${q.toString()}`);
}

export function getFile(bankId: string, path: string): Promise<FileResult> {
  const q = new URLSearchParams({ bank: bankId, path });
  return api(`/api/file?${q.toString()}`);
}

export function getStatus(): Promise<StatusResult> {
  return api("/api/status");
}
