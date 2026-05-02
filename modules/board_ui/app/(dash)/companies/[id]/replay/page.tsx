"use client";

import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";

interface EventRow {
  id: number;
  kind: string;
  ts: string;
  actor?: string | null;
  payload?: Record<string, unknown>;
}

interface LLMStats {
  total: number;
  failed: number;
  rate_limited: number;
  last_provider?: string | null;
  last_model?: string | null;
  last_at?: string | null;
}

export default function ReplayPage() {
  const params = useParams<{ id: string }>() ?? { id: "" };
  const cid = params.id;
  const [kind, setKind] = useState("");

  const q = useQuery({
    queryKey: ["company", cid, "events", kind],
    queryFn: () => {
      const qs = new URLSearchParams();
      if (kind) qs.set("kinds", kind);
      qs.set("limit", "200");
      return api<EventRow[]>(`/companies/${cid}/events?${qs.toString()}`);
    },
  });

  const stats = useQuery({
    queryKey: ["company", cid, "llm-stats"],
    queryFn: () => api<LLMStats>(`/companies/${cid}/llm/stats`),
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-4 p-6">
      <h1 className="text-lg font-semibold">Replay</h1>
      <Card>
        <div className="flex flex-wrap gap-6 text-sm">
          <div>
            <div className="text-xs text-muted">LLM istek (toplam)</div>
            <div className="font-mono text-base">{stats.data?.total ?? "—"}</div>
          </div>
          <div>
            <div className="text-xs text-muted">Failed</div>
            <div className="font-mono text-base">{stats.data?.failed ?? "—"}</div>
          </div>
          <div>
            <div className="text-xs text-muted">Rate-limited (429)</div>
            <div className="font-mono text-base">
              {stats.data?.rate_limited ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted">Provider / Model</div>
            <div className="font-mono text-base">
              {stats.data?.last_provider ?? "—"}
              {stats.data?.last_model ? ` / ${stats.data.last_model}` : ""}
            </div>
          </div>
          {stats.data?.last_at && (
            <div>
              <div className="text-xs text-muted">Son istek</div>
              <div className="font-mono text-xs">{stats.data.last_at}</div>
            </div>
          )}
        </div>
      </Card>
      <Card>
        <label className="text-xs text-muted">Kind filtre</label>
        <input
          value={kind}
          onChange={(e) => setKind(e.target.value)}
          placeholder="e.g. message,tool_call"
          className="mt-1 h-9 w-full rounded-md border bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-accent"
        />
      </Card>
      <Card>
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th className="py-1">#</th>
              <th className="py-1">kind</th>
              <th className="py-1">ts</th>
              <th className="py-1">payload</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs">
            {q.data?.map((e) => (
              <tr key={e.id} className="border-t align-top">
                <td className="py-1">{e.id}</td>
                <td className="py-1">{e.kind}</td>
                <td className="py-1">{e.ts}</td>
                <td className="max-w-xl truncate py-1">
                  {e.payload ? JSON.stringify(e.payload) : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {q.error && (
          <div className="text-xs text-red-600">
            {(q.error as Error).message}
          </div>
        )}
      </Card>
    </div>
  );
}
