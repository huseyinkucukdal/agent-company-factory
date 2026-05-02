"use client";

import { MonitorPanel } from "@/components/monitor-panel";
import { OrgChart } from "@/components/org-chart";
import { SummaryCards } from "@/components/summary-cards";
import { Card, StatusPill } from "@/components/ui/card";
import { api } from "@/lib/api";
import {
  effectiveCompanyStatus,
  type Agent,
  type CompanyDetail,
} from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";

export default function CompanyOverviewPage() {
  const params = useParams<{ id: string }>() ?? { id: "" };
  const cid = params.id;

  const company = useQuery({
    queryKey: ["company", cid],
    queryFn: () => api<CompanyDetail>(`/companies/${cid}`),
  });
  const agents = useQuery({
    queryKey: ["company", cid, "agents"],
    queryFn: () => api<Agent[]>(`/companies/${cid}/agents`),
  });

  const c = company.data;

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-xs text-muted">
            <Link href="/" className="hover:underline">
              Companies
            </Link>{" "}
            ›
          </div>
          <h1 className="text-xl font-semibold">{c?.name ?? cid}</h1>
          <div className="mt-1 flex items-center gap-2 text-xs text-muted">
            <StatusPill status={effectiveCompanyStatus(c)} />
            <span className="font-mono">{cid}</span>
            {c?.industry && <span>· {c.industry}</span>}
          </div>
        </div>
        <Link
          href={`/companies/${cid}/settings`}
          className="text-xs text-muted hover:text-fg hover:underline"
        >
          Lifecycle & settings →
        </Link>
      </header>

      <SummaryCards companyId={cid} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <Card className="p-3">
            <MonitorPanel companyId={cid} agents={agents.data ?? []} />
          </Card>
        </div>

        <div className="lg:col-span-2">
          <Card className="p-3">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm font-medium">Organization</div>
              <Link
                href={`/companies/${cid}/agents`}
                className="text-xs text-muted hover:text-fg hover:underline"
              >
                List →
              </Link>
            </div>
            {agents.isLoading ? (
              <div className="text-xs text-muted">Loading…</div>
            ) : (
              <OrgChart companyId={cid} agents={agents.data ?? []} />
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
