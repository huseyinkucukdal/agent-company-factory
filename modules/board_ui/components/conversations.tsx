"use client";

import {
  agentLabel,
  buildAgentMap,
  humanizeEvent,
} from "@/lib/humanize-event";
import type { Agent, StreamEvent } from "@/lib/types";
import { useMemo, useState } from "react";

interface ConversationsProps {
  events: StreamEvent[];
  agents?: Agent[];
}

interface Thread {
  /** correlation_id, or a synthetic key when missing. */
  key: string;
  /** Display label: "CEO → HR" when derivable. */
  label: string;
  /** First non-empty body or summary. */
  preview: string;
  /** All events in chronological order (id ascending). */
  events: StreamEvent[];
  /** Latest event id — drives sort order (newest first). */
  latestId: number;
}

/**
 * Group events by ``correlation_id`` and present each group as a
 * conversation thread. Threads with a single delivery-only event get
 * folded into the synthetic "no correlation" bucket so the panel does
 * not flood with one-line orphans.
 */
export function Conversations({ events, agents = [] }: ConversationsProps) {
  const agentMap = useMemo(() => buildAgentMap(agents), [agents]);

  const threads = useMemo(() => buildThreads(events, agentMap), [
    events,
    agentMap,
  ]);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected =
    threads.find((t) => t.key === selectedKey) ?? threads[0] ?? null;

  if (threads.length === 0) {
    return (
      <div className="rounded-md border bg-card p-6 text-center text-sm text-muted">
        No conversations yet. Once agents start exchanging messages or
        invoking tools, threads will appear here grouped by correlation_id.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-5">
      <div className="lg:col-span-2">
        <div className="rounded-md border bg-card">
          <div className="border-b p-2 text-xs text-muted">
            {threads.length} thread{threads.length === 1 ? "" : "s"}
          </div>
          <ul className="max-h-[440px] overflow-y-auto">
            {threads.map((t) => (
              <li key={t.key}>
                <button
                  onClick={() => setSelectedKey(t.key)}
                  className={
                    "w-full border-b px-3 py-2 text-left text-xs transition hover:bg-bg " +
                    (selected?.key === t.key ? "bg-bg" : "")
                  }
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium text-fg">{t.label}</span>
                    <span className="text-[10px] text-muted">
                      {t.events.length} ev
                    </span>
                  </div>
                  <div className="truncate text-muted">{t.preview}</div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="lg:col-span-3">
        <div className="rounded-md border bg-card">
          {selected ? (
            <>
              <div className="flex items-center justify-between gap-2 border-b p-2 text-xs">
                <div className="font-medium">{selected.label}</div>
                <div className="font-mono text-[10px] text-muted">
                  {selected.key.startsWith("__") ? "no correlation" : selected.key}
                </div>
              </div>
              <ol className="max-h-[440px] space-y-1 overflow-y-auto p-2">
                {selected.events.map((e) => {
                  const h = humanizeEvent(e, agentMap);
                  const actor =
                    (e as { actor_agent_id?: string }).actor_agent_id ?? null;
                  return (
                    <li
                      key={e.id}
                      className={
                        "rounded border p-2 text-xs " +
                        (h.muted ? "opacity-70" : "")
                      }
                    >
                      <div className="mb-1 flex items-center justify-between gap-2">
                        <span className={"font-mono " + h.tone}>
                          {h.icon} #{e.id} · {e.kind}
                        </span>
                        <span className="text-[10px] text-muted">
                          {actor ? agentLabel(actor, agentMap) : ""}
                        </span>
                      </div>
                      <div className="whitespace-pre-wrap text-fg">
                        {h.summary}
                      </div>
                      {h.detail && h.detail !== h.summary ? (
                        <div className="mt-1 whitespace-pre-wrap text-muted">
                          {h.detail}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ol>
            </>
          ) : (
            <div className="p-6 text-center text-sm text-muted">
              Select a thread on the left.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function buildThreads(
  events: StreamEvent[],
  agentMap: Map<string, Agent>,
): Thread[] {
  const byCorr = new Map<string, StreamEvent[]>();
  for (const e of events) {
    const corr =
      (e as { correlation_id?: string }).correlation_id ??
      (e.payload as { correlation_id?: string } | undefined)?.correlation_id ??
      null;
    const key = corr ?? `__solo_${e.id}`;
    const list = byCorr.get(key) ?? [];
    list.push(e);
    byCorr.set(key, list);
  }

  const threads: Thread[] = [];
  for (const [key, list] of byCorr.entries()) {
    list.sort((a, b) => a.id - b.id);
    const first = list[0];
    const latestId = list[list.length - 1]?.id ?? 0;

    const messageEv = list.find((e) => e.kind === "message.sent");
    const toolEv = list.find((e) => e.kind === "tool.called");
    const baseEv = messageEv ?? toolEv ?? first;

    const label = labelFor(baseEv, agentMap);
    const preview = previewFor(list, agentMap);

    threads.push({ key, label, preview, events: list, latestId });
  }

  threads.sort((a, b) => b.latestId - a.latestId);
  return threads;
}

function labelFor(
  ev: StreamEvent,
  agents: Map<string, Agent>,
): string {
  const p = (ev.payload ?? {}) as Record<string, unknown>;
  if (ev.kind === "message.sent") {
    const from = agentLabel(String(p.from_agent ?? ""), agents);
    const to = agentLabel(String(p.to_agent ?? ""), agents);
    return `${from} → ${to}`;
  }
  if (ev.kind === "tool.called") {
    const actor =
      (ev as { actor_agent_id?: string }).actor_agent_id ?? null;
    const tool = String(p.tool ?? "?");
    return `${agentLabel(actor, agents)} · ${tool}`;
  }
  if (ev.kind === "approval.requested") {
    return `Approval · ${String(p.kind ?? "?")}`;
  }
  return ev.kind;
}

function previewFor(
  events: StreamEvent[],
  agents: Map<string, Agent>,
): string {
  const messageEv = events.find((e) => e.kind === "message.sent");
  if (messageEv) {
    const body = String(
      (messageEv.payload as { content?: string } | undefined)?.content ?? "",
    );
    if (body) return truncate(body, 80);
  }
  const last = events[events.length - 1];
  if (!last) return "";
  return humanizeEvent(last, agents).summary;
}

function truncate(s: string, n: number): string {
  const flat = s.replace(/\s+/g, " ").trim();
  return flat.length <= n ? flat : flat.slice(0, n - 1) + "…";
}
