"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "./auth-store";
import type { StreamEvent } from "./types";

interface UseEventStreamOptions {
  enabled?: boolean;
  since?: number;
  /** Cap retained events to avoid memory blow-up. */
  bufferSize?: number;
}

/**
 * Resolve the SSE endpoint to a fully-qualified URL pointing at the
 * Board API. We deliberately bypass the Next.js dev rewrite for SSE: the
 * dev server's HTTP proxy buffers the chunked stream and frames never
 * reach the browser, which is why the Live Feed silently stayed empty
 * even while the backend was emitting events at 200 OK.
 *
 * Resolution order:
 *   1. ``NEXT_PUBLIC_API_BASE_URL`` (set in docker-compose for the UI).
 *   2. ``http://<current-hostname>:8000`` — works when the operator
 *      browses the UI on localhost while the API runs on port 8000.
 */
function resolveStreamUrl(path: string): string {
  const stripped = path.startsWith("/api/") ? path.slice(4) : path;
  const envBase =
    typeof process !== "undefined"
      ? process.env.NEXT_PUBLIC_API_BASE_URL
      : undefined;
  let base = envBase;
  if (!base && typeof window !== "undefined") {
    base = `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  if (!base) return stripped; // SSR fallback; effect runs only client-side anyway.
  return `${base.replace(/\/$/, "")}${stripped.startsWith("/") ? "" : "/"}${stripped}`;
}

/**
 * Subscribe to an SSE stream from the Board API.
 *
 * NOTE: EventSource cannot send custom headers, so we cannot put the JWT
 * in `Authorization`. Until the API accepts a query-string token, we
 * fall back to passing the access token via `?token=` (best-effort —
 * the API may ignore it). For internal/dev use this is acceptable.
 */
export function useEventStream(
  url: string | null,
  opts: UseEventStreamOptions = {},
) {
  const { enabled = true, since = 0, bufferSize = 1_000 } = opts;
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const lastIdRef = useRef<number>(since);
  const tokenRef = useRef<string | null>(null);

  // Keep the most recent token reference for reconnects.
  tokenRef.current = useAuth.getState().token?.access_token ?? null;

  useEffect(() => {
    if (!enabled || !url) return;
    let closed = false;
    let attempt = 0;
    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      const resolved = resolveStreamUrl(url);
      const u = new URL(resolved, window.location.origin);
      u.searchParams.set("since", String(lastIdRef.current));
      if (tokenRef.current) u.searchParams.set("token", tokenRef.current);
      es = new EventSource(u.toString());
      es.onopen = () => {
        attempt = 0;
        setConnected(true);
      };
      es.onmessage = (ev) => {
        if (!ev.data) return;
        try {
          const parsed = JSON.parse(ev.data) as StreamEvent;
          if (typeof parsed.id === "number") lastIdRef.current = parsed.id;
          setEvents((prev) => {
            const next = [...prev, parsed];
            return next.length > bufferSize
              ? next.slice(next.length - bufferSize)
              : next;
          });
        } catch {
          // ignore malformed frame
        }
      };
      es.onerror = () => {
        setConnected(false);
        es?.close();
        es = null;
        if (closed) return;
        attempt += 1;
        const backoff = Math.min(30_000, 1_000 * 2 ** Math.min(attempt, 5));
        retryTimer = setTimeout(connect, backoff);
      };
    };
    connect();
    return () => {
      closed = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
      setConnected(false);
    };
  }, [url, enabled, bufferSize]);

  return { events, connected, lastEventId: lastIdRef.current };
}
