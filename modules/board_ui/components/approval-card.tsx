"use client";

import { Button } from "@/components/ui/button";
import { Card, StatusPill } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import type { Approval } from "@/lib/types";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

interface Props {
  approval: Approval;
  onDecided?: () => void;
}

export function ApprovalCard({ approval, onDecided }: Props) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  const decide = useMutation({
    mutationFn: (decision: "approve" | "deny") =>
      api(
        `/companies/${approval.company_id}/approvals/${encodeURIComponent(approval.request_id)}/decide`,
        {
        method: "POST",
        json: { decision, note: note || null },
        },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      qc.invalidateQueries({ queryKey: ["company", approval.company_id, "approvals"] });
      onDecided?.();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.message : "Operation failed"),
  });

  const ageMs = Date.now() - new Date(approval.created_at).getTime();
  const ageMin = Math.max(0, Math.floor(ageMs / 60_000));

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="rounded bg-bg px-2 py-0.5 font-mono text-[11px]">
              {approval.kind}
            </span>
            <StatusPill status={approval.status} />
            {approval.require_security && (
              <span className="rounded bg-red-500/10 px-2 py-0.5 text-[11px] text-red-700 dark:text-red-300">
                security
              </span>
            )}
          </div>
          <div className="mt-1 font-mono text-[11px] text-muted">
            {approval.request_id} · {approval.company_id}
          </div>
        </div>
        <div className="text-right text-xs text-muted">
          <div>{ageMin}dk</div>
          <div>→ {approval.route_target}</div>
        </div>
      </div>

      <pre className="mt-3 max-h-40 overflow-auto rounded-md bg-bg p-2 font-mono text-[11px]">
        {JSON.stringify(approval.payload, null, 2)}
      </pre>

      {approval.status === "pending" && (
        <div className="mt-3 space-y-2">
          <input
            placeholder="Note (optional)"
            className="h-9 w-full rounded-md border bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-accent"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          {error && (
            <div className="text-xs text-red-600">{error}</div>
          )}
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="danger"
              disabled={decide.isPending}
              onClick={() => decide.mutate("deny")}
            >
              Deny
            </Button>
            <Button
              size="sm"
              disabled={decide.isPending}
              onClick={() => decide.mutate("approve")}
            >
              Approve
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
