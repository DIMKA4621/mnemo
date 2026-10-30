import { api } from "./fetcher";

/** One `GET/PUT /api/settings` field: value plus where it came from.
 *  Precedence is env > file, so `overridden` is not decoration — a value
 *  saved here can be completely inert while it is true. */
export interface SettingsValue<T> {
  value: T;
  source: string;
  env_var: string;
  overridden: boolean;
}

/** The fixed key set `GET /api/settings` reports (`api.api_settings`). A
 *  key can be briefly absent from a fresh install with no `settings.json`
 *  yet, hence every field is optional. */
export interface SettingsMap {
  provider?: SettingsValue<"local" | "api" | string>;
  auto_update?: SettingsValue<boolean>;
  require_login?: SettingsValue<boolean>;
  "api.url"?: SettingsValue<string>;
  "api.model"?: SettingsValue<string>;
  "api.dim"?: SettingsValue<number>;
  "api.timeout"?: SettingsValue<number>;
  "api.key_set"?: SettingsValue<boolean>;
}

/** One embedding model in the catalogue (`src/presets.py::ModelPreset`). */
export interface ModelPreset {
  name: string;
  label: string;
  dim: number;
  prefixed: boolean;
  note: string;
}

/** One backend the catalogue knows (`src/presets.py::BackendPreset`). */
export interface BackendPreset {
  id: string;
  label: string;
  provider: "local" | "api";
  url: string;
  needs_key: boolean;
  note: string;
  models: ModelPreset[];
}

export interface SettingsResult {
  path: string;
  exists: boolean;
  settings: SettingsMap;
  readonly: { api_host: string; api_port: number };
  presets: BackendPreset[];
  /** `PUT` only — never present on a plain `GET`. */
  restart_required?: boolean;
  /** `PUT` only, and only the instant a save turns `require_login` on
   *  (false -> true) — a one-time reveal, never echoed by a later `GET`. */
  service_token?: string;
}

export interface SettingsApiPayload {
  url?: string;
  model?: string;
  dim?: number;
  timeout?: number;
  /** Sent only when the key field was actually typed into — an absent key
   *  means "leave what is stored", not "clear it". */
  key?: string;
  passage_prefix?: string;
  query_prefix?: string;
}

export interface SettingsPutPayload {
  provider?: "local" | "api";
  auto_update?: boolean;
  require_login?: boolean;
  api?: SettingsApiPayload;
}

export interface AutostartResult {
  supported: boolean;
  enabled: boolean;
  mechanism: string;
  name: string;
}

/** `GET/POST /api/embed/{state,unload,load}` — shape varies by which
 *  backend is active (`local`/Ollama hold a model, a hosted `api` never
 *  does and reports `holding: "n/a"`), so most fields beyond `holding`/
 *  `model`/`cached`/`download` are optional. */
export interface EmbedStateResult {
  backend: string;
  model: string | null;
  holding: "loaded" | "unloaded" | "n/a" | "unknown";
  cached: boolean | null;
  where: string | null;
  wake_s: number | null;
  idle_timeout_s?: number | null;
  expires_at?: string | null;
  others_held?: number;
  probe_dim?: number;
  detail: string | null;
  download: { active: boolean; failed: boolean };
}

export interface DoctorReportEngine {
  home: string;
  state_dir: string;
  python: string;
}

export interface DoctorReportProvider {
  machine: string;
  overrides: string[];
  local_in_use: boolean;
}

export interface DoctorReportDisk {
  target: string;
  available_bytes: number;
  required_bytes: number;
  ok: boolean;
}

export interface DoctorReportResident {
  applicable: boolean;
  up: boolean | null;
  host: string;
  port: number;
  scope: string;
}

export interface DoctorReportEndpoint {
  applicable: boolean;
  configured?: boolean;
  url?: string | null;
  model?: string | null;
  dim?: number | null;
  key_set?: boolean;
  error?: string | null;
}

export interface DoctorReportBackend {
  up: boolean;
  url: string;
  scope: string;
  error: string | null;
  serving_pid: number | null;
  launcher_pid: number | null;
  banks: number | null;
  queue_depth: number;
}

export interface DoctorReportToken {
  present: boolean;
  source: string | null;
  where: string | null;
  scope: string | null;
  login_required?: boolean;
}

export interface DoctorReportRegistryBank {
  id: string;
  name: string;
  root: string;
  state: string;
  exists: boolean;
}

export interface DoctorReportRegistry {
  ok: boolean;
  error: string | null;
  count: number | null;
  banks: DoctorReportRegistryBank[];
}

export interface DoctorReportOrphan {
  id: string;
  path: string;
  size: number;
  root: string | null;
  root_exists: boolean;
  schema: string | null;
  files: number | null;
  last_indexed: string | null;
  error: string | null;
}

export interface DoctorReportOrphans {
  ok: boolean;
  error: string | null;
  count: number | null;
  bytes: number | null;
  items: DoctorReportOrphan[];
}

export interface DoctorReportStaleProject {
  root: string;
  command: string;
  reason: string;
  migrate: boolean;
}

export interface DoctorReportWiring {
  ok: boolean;
  error: string | null;
  total: number | null;
  stale: DoctorReportStaleProject[];
}

export interface DoctorReportSelfUpdate {
  current_tag: string | null;
  last_check_at: string | null;
  update_available: boolean;
  latest_tag: string | null;
  stuck_apply: { tag: string | null; started_at: string; elapsed_s: number } | null;
}

/** `GET /api/doctor` — mirrors `src/diagnostics.py::collect()`. `disk` and
 *  `self_update` are optional: the dev fixture (`devserver.py`) does not
 *  fabricate either, so a component reading this report must not assume
 *  they are always present. */
export interface DoctorResult {
  engine: DoctorReportEngine;
  provider: DoctorReportProvider;
  model: { cached: boolean; needed: boolean };
  disk?: DoctorReportDisk;
  sqlite_vec: { ok: boolean; error: string | null };
  resident: DoctorReportResident;
  endpoint: DoctorReportEndpoint;
  backend: DoctorReportBackend;
  token: DoctorReportToken;
  registry: DoctorReportRegistry;
  orphans: DoctorReportOrphans;
  wiring: DoctorReportWiring;
  self_update?: DoctorReportSelfUpdate;
}

export interface CleanOrphansResult {
  requested: string[];
  removed: { id: string; files_removed: number; bytes: number }[];
  skipped: { id: string; reason: string }[];
  locked: { id: string; paths: string[] }[];
  freed_bytes: number;
}

/** One `/api/update/*` apply cycle's state — `GET /api/update/status`'s
 *  `apply` block, and the same shape a `POST /api/update/apply` failure is
 *  synthesised into locally (see `UpdateModal`'s `confirmApply`). `trigger`
 *  is absent on the dev fixture's initial idle state, hence optional. */
export interface UpdateApplyState {
  state: "idle" | "staging" | "switching" | "done" | "failed" | "rolled_back";
  tag: string | null;
  step: "download" | "venv" | "done" | "failed" | null;
  detail?: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  trigger?: "manual" | "auto" | null;
}

export interface AutoPendingInfo {
  tag: string;
  /** Absent on the WS-sourced patch (`update_auto_pending`'s `"started"`
   *  payload carries no `started_at`) — only `GET /api/update/status`'s own
   *  response fills it in. Nothing renders it either way. */
  started_at?: string | null;
  deadline: string;
  seconds_left: number;
}

export interface UpdateAutoStatus {
  enabled: boolean;
  pending: AutoPendingInfo | null;
  blacklist: { tag: string; attempts: number; blacklisted: boolean; last_error: string | null; last_failed_at: string | null; next_retry_at: string | null }[];
}

export interface UpdateStatusResult {
  current: { tag: string | null; installed_at: string | null; commit: string | null };
  latest_known: { tag: string | null; checked_at: string | null; update_available: boolean };
  check: { in_progress: boolean; error: string | null };
  apply: UpdateApplyState;
  history: { tag: string; installed_at: string; commit: string; status: string }[];
  retention: { keep: number };
  auto: UpdateAutoStatus;
}

export interface UpdateCheckResult {
  latest_tag: string | null;
  current_tag: string | null;
  update_available: boolean;
  checked_at: string | null;
  error: string | null;
}

export function getSettings(): Promise<SettingsResult> {
  return api("/api/settings");
}

export function putSettings(payload: SettingsPutPayload): Promise<SettingsResult> {
  return api("/api/settings", { method: "PUT", body: payload });
}

export function getAutostart(): Promise<AutostartResult> {
  return api("/api/autostart");
}

export function setAutostart(enabled: boolean): Promise<AutostartResult> {
  return api("/api/autostart", { method: "POST", body: { enabled } });
}

export function getEmbedState(): Promise<EmbedStateResult> {
  return api("/api/embed/state");
}

export function downloadEmbedModel(): Promise<{ started: boolean }> {
  return api("/api/embed/download", { method: "POST" });
}

export function unloadEmbed(): Promise<EmbedStateResult> {
  return api("/api/embed/unload", { method: "POST" });
}

export function loadEmbed(): Promise<EmbedStateResult> {
  return api("/api/embed/load", { method: "POST" });
}

export function getDoctor(): Promise<DoctorResult> {
  return api("/api/doctor");
}

export function cleanOrphans(ids: string[]): Promise<CleanOrphansResult> {
  return api("/api/clean-orphans", { method: "POST", body: { ids } });
}

export function getUpdateStatus(): Promise<UpdateStatusResult> {
  return api("/api/update/status");
}

export function checkForUpdate(): Promise<UpdateCheckResult> {
  return api("/api/update/check", { method: "POST" });
}

export function applyUpdate(tag: string): Promise<{ accepted: boolean; tag: string }> {
  return api("/api/update/apply", { method: "POST", body: { tag } });
}

export function confirmAutoPending(): Promise<{ accepted: boolean; tag: string }> {
  return api("/api/update/auto/confirm", { method: "POST" });
}

export function cancelAutoPending(): Promise<{ accepted: boolean; tag: string }> {
  return api("/api/update/auto/cancel", { method: "POST" });
}
