"use client";

import { Card, StatusPill } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";

export default function AgentsPage() {
  const params = useParams<{ id: string }>() ?? { id: "" };
  const cid = params.id;
  const q = useQuery({
    queryKey: ["company", cid, "agents", "all"],
    queryFn: () => api<Agent[]>(`/companies/${cid}/agents`),
  });

  return (
    <div className="p-6">
      <h1 className="mb-4 text-lg font-semibold">Ekip</h1>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {q.data?.map((a) => (
          <Link key={a.id} href={`/companies/${cid}/agents/${a.id}`}>
            <Card className="transition hover:border-accent">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-medium">{a.role_title || a.role}</div>
                  {(a.first_name || a.last_name) && (
                    <div className="text-xs text-muted">
                      {[a.first_name, a.last_name].filter(Boolean).join(" ")}
                    </div>
                  )}
                  <div className="font-mono text-[10px] text-muted">{a.id}</div>
                </div>
                <StatusPill status={a.status} />
              </div>
              {a.reports_to && (
                <div className="mt-2 text-xs text-muted">
                  Reports to: <span className="font-mono">{a.reports_to}</span>
                </div>
              )}
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
