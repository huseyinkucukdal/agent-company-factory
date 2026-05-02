"use client";

import { agentLabel, buildAgentMap } from "@/lib/humanize-event";
import { api } from "@/lib/api";
import type { Agent, StreamEvent } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

interface AgentConversationsProps {
  companyId: string;
  agentId: string;
  agents: Agent[];
}

type Direction = "all" | "sent" | "received";

interface MessageRow {
  id: number;
  ts: string | null;
  fromId: string;
  toId: string;
  body: string;
  direction: "sent" | "received";
}

/**
 * List of every message this agent sent or received, oldest-first.
 *
 * Backend ``/events`` endpoint supports filtering by `kinds` and `actor`,
 * but not by recipient inside the payload. So we fetch all
 * `message.sent` events with a generous limit and filter client-side for
 * sender == agentId OR payload.to_agent == agentId. This is fine at
 * single-company scale (tens of thousands of events at most).
 */
export function AgentConversations({
  companyId,
  agentId,
  agents,
}: AgentConversationsProps) {
  const agentMap = useMemo(() => buildAgentMap(agents), [agents]);
  const [direction, setDirection] = useState<Direction>("all");
  const [partnerId, setPartnerId] = useState<string>("");

  const events = useQuery({
    queryKey: ["company", companyId, "messages-for", agentId],
    queryFn: () =>
      api<StreamEvent[]>(
        `/companies/${companyId}/events?kinds=message.sent&limit=2000`,
      ),
    refetchInterval: 5_000,
  });

  const rows = useMemo<MessageRow[]>(() => {
    const out: MessageRow[] = [];
    for (const e of events.data ?? []) {
      const p = (e.payload ?? {}) as Record<string, unknown>;
      const fromId = String(p.from_agent ?? "");
      const toId = String(p.to_agent ?? "");
      const body = String(p.content ?? "");
      const ts =
        (e as { ts_real?: string; ts?: string }).ts_real ??
        (e as { ts?: string }).ts ??
        null;

      if (fromId === agentId) {
        out.push({ id: e.id, ts, fromId, toId, body, direction: "sent" });
      } else if (toId === agentId) {
        out.push({ id: e.id, ts, fromId, toId, body, direction: "received" });
      }
    }
    out.sort((a, b) => a.id - b.id);
    return out;
  }, [events.data, agentId]);

  const partners = useMemo(() => {
    const set = new Set<string>();
    for (const r of rows) {
      const other = r.direction === "sent" ? r.toId : r.fromId;
      if (other) set.add(other);
    }
    return [...set];
  }, [rows]);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (direction !== "all" && r.direction !== direction) return false;
      if (partnerId) {
        const other = r.direction === "sent" ? r.toId : r.fromId;
        if (other !== partnerId) return false;
      }
      return true;
    });
  }, [rows, direction, partnerId]);

  const sentCount = rows.filter((r) => r.direction === "sent").length;
  const receivedCount = rows.length - sentCount;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">
          {sentCount} sent · {receivedCount} received
        </span>
        <div className="ml-auto flex flex-wrap gap-2">
          <select
            value={direction}
            onChange={(e) => setDirection(e.target.value as Direction)}
            className="h-8 rounded-md border bg-card px-2"
          >
            <option value="all">All</option>
            <option value="sent">Sent only</option>
            <option value="received">Received only</option>
          </select>
          <select
            value={partnerId}
            onChange={(e) => setPartnerId(e.target.value)}
            className="h-8 rounded-md border bg-card px-2"
          >
            <option value="">Anyone</option>
            {partners.map((id) => (
              <option key={id} value={id}>
                {agentLabel(id, agentMap)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {events.isLoading ? (
        <div className="rounded-md border p-3 text-sm text-muted">
          Loading messages…
        </div>
      ) : events.error ? (
        <div className="rounded-md border border-red-500/40 p-3 text-sm text-red-600">
          {(events.error as Error).message}
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-md border p-3 text-sm text-muted">
          {rows.length === 0
            ? "This agent has not sent or received any messages yet."
            : "No messages match the current filters."}
        </div>
      ) : (
        <ol className="max-h-[520px] space-y-2 overflow-y-auto rounded-md border p-2">
          {filtered.map((r) => {
            const counterId = r.direction === "sent" ? r.toId : r.fromId;
            const counterLabel = agentLabel(counterId, agentMap);
            const tone =
              r.direction === "sent"
                ? "border-blue-500/40 bg-blue-500/5"
                : "border-emerald-500/40 bg-emerald-500/5";
            const arrow = r.direction === "sent" ? "→" : "←";
            return (
              <li
                key={r.id}
                className={"rounded-md border p-2 text-xs " + tone}
              >
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="font-mono">
                    #{r.id} · {arrow} {counterLabel}
                  </span>
                  <span className="text-[10px] text-muted">
                    {r.ts ? new Date(r.ts).toLocaleString() : ""}
                  </span>
                </div>
                <div className="whitespace-pre-wrap text-fg">{r.body}</div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
