"use client";

import { Button } from "@/components/ui/button";
import { Card, StatusPill } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Company } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

export default function CompaniesPage() {
  const q = useQuery({
    queryKey: ["companies"],
    queryFn: () => api<Company[]>("/companies"),
  });

  return (
    <div className="p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Companies</h1>
          <p className="text-sm text-muted">
            Active companies and their statuses
          </p>
        </div>
        <Link href="/companies/new">
          <Button>+ New company</Button>
        </Link>
      </div>
      {q.isLoading && <div className="text-sm text-muted">Loading…</div>}
      {q.error && (
        <div className="rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-700">
          {(q.error as Error).message}
        </div>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {q.data?.map((c) => (
          <Link key={c.id} href={`/companies/${c.id}`}>
            <Card className="transition hover:border-accent">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium">{c.name}</div>
                  <div className="font-mono text-[11px] text-muted">{c.id}</div>
                </div>
                <StatusPill status={c.status} />
              </div>
              <div className="mt-3 text-xs text-muted">
                Created: {new Date(c.created_at).toLocaleString()}
              </div>
            </Card>
          </Link>
        ))}
        {q.data && q.data.length === 0 && (
          <div className="text-sm text-muted">No companies yet.</div>
        )}
      </div>
    </div>
  );
}
