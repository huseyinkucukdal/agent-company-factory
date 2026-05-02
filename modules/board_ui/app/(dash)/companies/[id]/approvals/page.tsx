"use client";

import { ApprovalCard } from "@/components/approval-card";
import { api } from "@/lib/api";
import type { Approval } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";

export default function CompanyApprovalsPage() {
  const params = useParams<{ id: string }>() ?? { id: "" };
  const cid = params.id;
  const q = useQuery({
    queryKey: ["company", cid, "approvals"],
    queryFn: () =>
      api<Approval[]>(`/companies/${cid}/approvals`).catch(() =>
        api<Approval[]>(`/approvals/pending`).then((all) =>
          all.filter((a) => a.company_id === cid),
        ),
      ),
    refetchInterval: 10_000,
  });

  return (
    <div className="p-6">
      <h1 className="mb-4 text-lg font-semibold">Company approvals</h1>
      <div className="space-y-3">
        {q.data?.map((a) => (
          <ApprovalCard key={a.request_id} approval={a} />
        ))}
        {q.data && q.data.length === 0 && (
          <div className="text-sm text-muted">No approvals for this company.</div>
        )}
      </div>
    </div>
  );
}
