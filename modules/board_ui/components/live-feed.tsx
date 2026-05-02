"use client";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  agentLabel,
  buildAgentMap,
  humanizeEvent,
  type Humanized,
} from "@/lib/humanize-event";
import type { Agent, StreamEvent } from "@/lib/types";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";

interface LiveFeedProps {
  events: StreamEvent[];
  connected: boolean;
  agents?: Agent[];
}

const ROW_H = 28;

export function LiveFeed({ events, connected, agents = [] }: LiveFeedProps) {
  const agentMap = useMemo(() => buildAgentMap(agents), [agents]);

  const [kindFilter, setKindFilter] = useState<Set<string>>(new Set());
  const [agentFilter, setAgentFilter] = useState<string>("");
  const [corrFilter, setCorrFilter] = useState<string>("");
  const [hideNoise, setHideNoise] = useState(true);
  const [autoscroll, setAutoscroll] = useState(true);
  const [selected, setSelected] = useState<StreamEvent | null>(null);

  const knownKinds = useMemo(() => {
    const set = new Set<string>();
    for (const e of events) set.add(e.kind);
    return [...set].sort();
  }, [events]);

  // Pre-humanize once per event change. Cached as a Map<eventId, Humanized>.
  const humanizedById = useMemo(() => {
    const m = new Map<number, Humanized>();
    for (const e of events) m.set(e.id, humanizeEvent(e, agentMap));
    return m;
  }, [events, agentMap]);

  const filtered = useMemo(() => {
    return events.filter((e) => {
      const h = humanizedById.get(e.id);
      if (hideNoise && h?.muted) return false;
      if (kindFilter.size > 0 && !kindFilter.has(e.kind)) return false;
      if (agentFilter) {
        const actor =
          (e as { actor_agent_id?: string }).actor_agent_id ??
          (e as { actor?: string }).actor ??
          "";
        if (actor !== agentFilter) return false;
      }
      if (corrFilter) {
        const corr =
          (e as { correlation_id?: string }).correlation_id ??
          (e.payload as { correlation_id?: string } | undefined)
            ?.correlation_id ??
          "";
        if (!corr.includes(corrFilter)) return false;
      }
      return true;
    });
  }, [events, humanizedById, hideNoise, kindFilter, agentFilter, corrFilter]);

  const parentRef = useRef<HTMLDivElement>(null);
  const virt = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_H,
    overscan: 12,
  });

  const [pinned, setPinned] = useState(true);
  const onScroll = () => {
    const el = parentRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    setPinned(distance < 24);
  };

  const lastCount = useRef(0);
  useEffect(() => {
    const el = parentRef.current;
    if (!el) return;
    const grew = filtered.length > lastCount.current;
    lastCount.current = filtered.length;
    if (grew && autoscroll && pinned) {
      el.scrollTop = el.scrollHeight;
    }
  }, [filtered.length, autoscroll, pinned]);

  const newSinceFreeze = !pinned
    ? Math.max(0, filtered.length - lastCount.current)
    : 0;

  const jumpToBottom = () => {
    const el = parentRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    setPinned(true);
  };

  const thread = useMemo<StreamEvent[]>(() => {
    if (!selected) return [];
    const corr =
      (selected as { correlation_id?: string }).correlation_id ??
      (selected.payload as { correlation_id?: string } | undefined)
        ?.correlation_id ??
      null;
    if (!corr) return [selected];
    return events.filter((e) => {
      const c =
        (e as { correlation_id?: string }).correlation_id ??
        (e.payload as { correlation_id?: string } | undefined)
          ?.correlation_id ??
        null;
      return c === corr;
    });
  }, [selected, events]);

  const emptyMessage = (() => {
    if (!connected) return "Reconnecting to live stream…";
    if (events.length === 0) return "Connected · waiting for the first event.";
    if (filtered.length === 0)
      return "All events filtered out — clear filters or toggle 'show all' to see them.";
    return null;
  })();

  return (
    <>
      <div className="flex flex-col rounded-md border bg-card">
        <div className="flex flex-wrap items-center gap-2 border-b p-2 text-xs">
          <ConnectionBadge connected={connected} />

          <span className="text-muted">
            {filtered.length}/{events.length}
          </span>

          <Popover>
            <PopoverTrigger asChild>
              <Button variant="secondary" size="sm">
                Type{kindFilter.size > 0 ? ` (${kindFilter.size})` : ""}
              </Button>
            </PopoverTrigger>
            <PopoverContent className="max-h-72 w-56 overflow-y-auto">
              {knownKinds.length === 0 ? (
                <div className="px-2 py-1 text-muted">No events yet.</div>
              ) : (
                <ul className="space-y-1">
                  {knownKinds.map((k) => (
                    <li key={k}>
                      <label className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-bg">
                        <input
                          type="checkbox"
                          checked={kindFilter.has(k)}
                          onChange={(ev) =>
                            setKindFilter((prev) => {
                              const next = new Set(prev);
                              if (ev.target.checked) next.add(k);
                              else next.delete(k);
                              return next;
                            })
                          }
                        />
                        <span className="font-mono">{k}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              {kindFilter.size > 0 ? (
                <div className="mt-2 border-t pt-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setKindFilter(new Set())}
                  >
                    Clear
                  </Button>
                </div>
              ) : null}
            </PopoverContent>
          </Popover>

          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="h-8 rounded-md border bg-card px-2 text-xs"
          >
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.role.toUpperCase()} · {a.id.slice(0, 8)}
              </option>
            ))}
          </select>

          <Input
            placeholder="correlation_id"
            value={corrFilter}
            onChange={(e) => setCorrFilter(e.target.value)}
            className="h-8 w-[180px] text-xs"
          />

          <label className="flex cursor-pointer items-center gap-1 text-muted">
            <input
              type="checkbox"
              checked={hideNoise}
              onChange={(e) => setHideNoise(e.target.checked)}
            />
            hide noise
          </label>
          <label className="ml-auto flex cursor-pointer items-center gap-1 text-muted">
            <input
              type="checkbox"
              checked={autoscroll}
              onChange={(e) => setAutoscroll(e.target.checked)}
            />
            auto-scroll
          </label>
        </div>

        <div className="relative">
          <div
            ref={parentRef}
            onScroll={onScroll}
            className="h-[480px] overflow-y-auto bg-bg p-1 font-mono text-xs"
          >
            {emptyMessage ? (
              <div className="p-3 text-muted">{emptyMessage}</div>
            ) : (
              <div
                style={{
                  height: virt.getTotalSize(),
                  width: "100%",
                  position: "relative",
                }}
              >
                {virt.getVirtualItems().map((vi) => {
                  const ev = filtered[vi.index];
                  const h =
                    humanizedById.get(ev.id) ??
                    humanizeEvent(ev, agentMap);
                  return (
                    <button
                      key={ev.id}
                      onClick={() => setSelected(ev)}
                      style={{
                        position: "absolute",
                        top: 0,
                        left: 0,
                        right: 0,
                        height: vi.size,
                        transform: `translateY(${vi.start}px)`,
                      }}
                      className={
                        "flex w-full items-center gap-2 border-b border-border/40 px-2 text-left hover:bg-card " +
                        (h.muted ? "opacity-60" : "")
                      }
                    >
                      <span className="w-12 shrink-0 text-muted">
                        #{ev.id}
                      </span>
                      <span className="w-5 shrink-0 text-center">
                        {h.icon}
                      </span>
                      <span className={"flex-1 truncate " + h.tone}>
                        {h.summary}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          {!pinned && newSinceFreeze > 0 ? (
            <button
              onClick={jumpToBottom}
              className="absolute bottom-3 right-3 rounded-full bg-accent px-3 py-1 text-xs font-medium text-white shadow-md hover:opacity-90"
            >
              ↓ {newSinceFreeze} new
            </button>
          ) : null}
        </div>
      </div>

      <Dialog
        open={selected !== null}
        onOpenChange={(o) => !o && setSelected(null)}
      >
        <DialogContent
          title={selected ? `#${selected.id} · ${selected.kind}` : "Event"}
          description={
            thread.length > 1 ? `${thread.length}-event thread` : undefined
          }
          className="w-[min(92vw,720px)]"
        >
          {selected ? (
            <div className="max-h-[60vh] space-y-2 overflow-y-auto">
              {thread.map((e) => {
                const h = humanizeEvent(e, agentMap);
                const actor =
                  (e as { actor_agent_id?: string }).actor_agent_id ?? null;
                return (
                  <div
                    key={e.id}
                    className={
                      "rounded-md border p-2 text-xs " +
                      (e.id === selected.id ? "border-accent bg-bg" : "")
                    }
                  >
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className={"font-mono " + h.tone}>
                        {h.icon} #{e.id} · {e.kind}
                      </span>
                      <span className="text-[10px] text-muted">
                        {actor ? agentLabel(actor, agentMap) : ""}
                        {e.ts ? ` · ${e.ts}` : ""}
                      </span>
                    </div>
                    <div className="mb-1 text-fg">{h.summary}</div>
                    {h.detail ? (
                      <div className="mb-1 whitespace-pre-wrap text-muted">
                        {h.detail}
                      </div>
                    ) : null}
                    <pre className="overflow-x-auto whitespace-pre-wrap break-words text-[11px] text-muted">
                      {JSON.stringify(e.payload ?? {}, null, 2)}
                    </pre>
                  </div>
                );
              })}
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

function ConnectionBadge({ connected }: { connected: boolean }) {
  return (
    <span
      className={
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium " +
        (connected
          ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
          : "bg-amber-500/15 text-amber-700 dark:text-amber-300")
      }
    >
      <span
        className={
          "h-1.5 w-1.5 rounded-full " +
          (connected ? "bg-emerald-500" : "bg-amber-500")
        }
      />
      {connected ? "live" : "reconnecting…"}
    </span>
  );
}
