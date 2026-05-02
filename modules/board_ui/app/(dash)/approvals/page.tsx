"use client";

import { ApprovalCard } from "@/components/approval-card";
import { api } from "@/lib/api";
import type { Approval } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";

export default function ApprovalsPage() {
  const q = useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () => api<Approval[]>("/approvals/pending"),
    refetchInterval: 10_000,
  });

  return (
    <div className="p-6">
      <header className="mb-6">
        <h1 className="text-xl font-semibold">Pending approvals</h1>
        <p className="text-sm text-muted">
          Requests this user can decide on
        </p>
      </header>
      {q.isLoading && <div className="text-sm text-muted">Loading…</div>}
      {q.error && (
        <div className="text-sm text-red-600">{(q.error as Error).message}</div>
      )}
      <div className="space-y-3">
        {q.data?.map((a) => (
          <ApprovalCard key={a.request_id} approval={a} />
        ))}
        {q.data && q.data.length === 0 && (
          <div className="text-sm text-muted">No pending approvals 🎉</div>
        )}
      </div>
    </div>
  );
}
