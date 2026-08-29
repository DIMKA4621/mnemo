import { api } from "./fetcher";

/** One vector/lexical hit inside a query event (contract 7.2). `content` is
 *  a per-event snapshot of the chunk text at query time — rows written
 *  before the field existed simply lack it, same as `null`. */
export interface LogHit {
  chunk_uid: string;
  path: string;
  heading: string | null;
  chunk_index: number;
  score: number;
  sim: number | null;
  content?: string | null;
}

export interface QueryLogEvent {
  id: number;
  ts: string;
  bank_id: string;
  face: string;
  query: string;
  path_prefix: string | null;
  status: "ready" | "indexing" | "empty";
  n_hits: number;
  took_ms: number;
  hits: LogHit[];
}

export interface IndexLogEvent {
  id: number;
  ts: string;
  bank_id: string;
  kind: "file" | "bulk" | "rebuild" | "prune";
  trigger: string;
  path: string | null;
  result: "ok" | "skipped" | "error";
  files_indexed: number;
  chunks_indexed: number;
  files_pruned: number;
  took_ms: number;
  error: string | null;
}

export interface LogsResult<E> {
  kind: "query" | "index";
  total: number;
  limit: number;
  offset: number;
  events: E[];
}

export interface GetLogsParams {
  kind: "query" | "index";
  bank?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export function getLogs<E = QueryLogEvent | IndexLogEvent>(
  params: GetLogsParams,
): Promise<LogsResult<E>> {
  const q = new URLSearchParams({ kind: params.kind });
  if (params.bank) q.set("bank", params.bank);
  if (params.since) q.set("since", params.since);
  if (params.until) q.set("until", params.until);
  q.set("limit", String(params.limit ?? 200));
  q.set("offset", String(params.offset ?? 0));
  return api(`/api/logs?${q.toString()}`);
}
