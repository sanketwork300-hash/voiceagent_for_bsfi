import { ApiError, messageForStatus } from "./errors";

/**
 * All REST traffic goes through the same-origin BFF (`/api/backend/*`), which attaches the staff token from an
 * httpOnly cookie. Customer calls pass their short-lived session token explicitly.
 */
export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  form?: FormData;
  sessionToken?: string;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
}

export const BFF_PREFIX = "/api/backend";

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const base = typeof window === "undefined" ? "http://localhost" : window.location.origin;
  const url = new URL(`${BFF_PREFIX}${path}`, base);
  for (const [k, v] of Object.entries(query ?? {})) if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  return typeof window === "undefined" ? url.toString() : url.pathname + url.search;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.sessionToken) headers["X-Session-Token"] = opts.sessionToken;
  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method: opts.method ?? (opts.body || opts.form ? "POST" : "GET"),
      headers,
      body: opts.form ?? (opts.body !== undefined ? JSON.stringify(opts.body) : undefined),
      signal: opts.signal,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, messageForStatus(0), null);
  }
  const reference = res.headers.get("x-request-id");
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = undefined;
  try {
    data = text ? JSON.parse(text) : undefined;
  } catch {
    data = undefined;
  }
  if (!res.ok) {
    const detail = typeof (data as { detail?: unknown })?.detail === "string" ? (data as { detail: string }).detail : undefined;
    if (res.status === 401 && typeof window !== "undefined" && !opts.sessionToken) {
      window.dispatchEvent(new CustomEvent("bfsi:unauthorized"));
    }
    throw new ApiError(res.status, messageForStatus(res.status), reference, detail);
  }
  return data as T;
}
