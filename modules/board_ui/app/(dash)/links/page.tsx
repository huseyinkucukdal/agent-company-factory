"use client";

import { Card, StatusPill } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

interface Link {
  id: string;
  src_company_id: string;
  dst_company_id: string;
  relation: string;
  status: string;
  created_at: string;
}

export default function LinksPage() {
  const q = useQuery({
    queryKey: ["links"],
    queryFn: () => api<Link[]>("/links"),
  });
  return (
    <div className="space-y-3 p-6">
      <h1 className="text-lg font-semibold">Inter-company connections</h1>
      {q.data?.map((l) => (
        <Card key={l.id}>
          <div className="flex items-center justify-between">
            <div className="font-mono text-xs">
              {l.src_company_id} → {l.dst_company_id}{" "}
              <span className="text-muted">({l.relation})</span>
            </div>
            <StatusPill status={l.status} />
          </div>
        </Card>
      ))}
      {q.data && q.data.length === 0 && (
        <div className="text-sm text-muted">No connections.</div>
      )}
    </div>
  );
}
