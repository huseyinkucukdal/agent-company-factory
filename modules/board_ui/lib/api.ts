"use client";

import { useAuth } from "./auth-store";
import type { Token } from "./types";

const API_PREFIX = "/api"; // proxied to Board API by next.config.mjs

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

interface FetchOptions extends RequestInit {
  json?: unknown;
  /** When true, do NOT attempt token refresh on 401. */
  noRefresh?: boolean;
}

let refreshInflight: Promise<Token | null> | null = null;

async function refreshTokens(): Promise<Token | null> {
  if (refreshInflight) return refreshInflight;
  const current = useAuth.getState().token;
  if (!current?.refresh_token) return null;
  refreshInflight = (async () => {
    try {
      const res = await fetch(`${API_PREFIX}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: current.refresh_token }),
      });
      if (!res.ok) {
        useAuth.getState().clear();
        return null;
      }
      const tok = (await res.json()) as Token;
      useAuth.getState().setToken(tok);
      return tok;
    } catch {
      useAuth.getState().clear();
      return null;
    } finally {
      refreshInflight = null;
    }
  })();
  return refreshInflight;
}

async function parseError(res: Response): Promise<ApiError> {
  let code: string | undefined;
  let message = res.statusText;
  try {
    const body = await res.json();
    const detail = body?.detail ?? body;
    if (typeof detail === "object" && detail !== null) {
      code = detail.code ?? body.code;
      message = detail.error ?? body.error ?? message;
    } else if (typeof detail === "string") {
      message = detail;
    }
  } catch {
    // not JSON
  }
  return new ApiError(message, res.status, code);
}

export async function api<T>(path: string, opts: FetchOptions = {}): Promise<T> {
  const { json, noRefresh, headers, ...rest } = opts;
  const buildHeaders = (token?: Token | null): HeadersInit => {
    const h: Record<string, string> = { ...(headers as Record<string, string>) };
    if (json !== undefined) h["Content-Type"] = "application/json";
    if (token) h["Authorization"] = `Bearer ${token.access_token}`;
    return h;
  };

  const doFetch = (token?: Token | null) =>
    fetch(`${API_PREFIX}${path}`, {
      ...rest,
      headers: buildHeaders(token),
      body: json !== undefined ? JSON.stringify(json) : (rest.body as BodyInit | undefined),
    });

  let res = await doFetch(useAuth.getState().token);
  if (res.status === 401 && !noRefresh) {
    const fresh = await refreshTokens();
    if (fresh) res = await doFetch(fresh);
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}
