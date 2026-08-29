import { useTokenStore } from "../store/token";

/** Contract 9.2: a single error envelope for every `/api` endpoint.
 *
 * `detail` is `Record<string, unknown> | null`, not `string`: the backend's
 * `ApiError.__init__(self, code, message, **detail)` (`src/api.py`) always
 * sends a JSON object (e.g. `stale_target`'s `{tag, latest_tag}`), never a
 * bare string — Settings' self-update flow (MN-36) is the first caller that
 * actually reads a field off it (`update.confirm.staleTarget`'s `latest_tag`). */
export class ApiError extends Error {
  code: string;
  detail: Record<string, unknown> | null;
  httpStatus: number;

  constructor(code: string, message: string, detail: Record<string, unknown> | null, httpStatus: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.detail = detail;
    this.httpStatus = httpStatus;
  }
}

export function isAuthError(err: unknown): err is ApiError {
  return err instanceof ApiError && (err.httpStatus === 401 || err.code === "unauthorized");
}

interface ApiOptions {
  method?: string;
  body?: unknown;
}

/**
 * Same contract as the vanilla console's `api()` helper
 * (`src/webui/static/app.js`): `Authorization: Bearer <token>` when we have
 * one, JSON in and out, `ApiError` on anything but 2xx. A 401 opens the
 * token gate centrally here so no individual query needs to know about it —
 * `hydrated` is checked instead of the store's `token` to avoid a false
 * "rejected" on a request fired before `hydrate()` has read `?token=`/
 * `sessionStorage` on mount.
 */
export async function api<T = unknown>(path: string, options?: ApiOptions): Promise<T> {
  const { token, hydrated, openGate } = useTokenStore.getState();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (options?.body !== undefined) headers["Content-Type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(path, {
      method: options?.method || "GET",
      headers,
      body: options?.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch (err) {
    throw new ApiError(
      "unreachable",
      err instanceof Error ? err.message : String(err),
      null,
      0,
    );
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new ApiError("internal", "invalid JSON from server", { raw: text.slice(0, 200) }, response.status);
    }
  }

  if (!response.ok) {
    const box = (payload as { error?: { code?: string; message?: string; detail?: Record<string, unknown> } } | null)
      ?.error || {};
    const err = new ApiError(
      box.code || "internal",
      box.message || response.statusText,
      box.detail || null,
      response.status,
    );
    if (isAuthError(err) && hydrated) {
      openGate("rejected");
    }
    throw err;
  }
  return payload as T;
}
