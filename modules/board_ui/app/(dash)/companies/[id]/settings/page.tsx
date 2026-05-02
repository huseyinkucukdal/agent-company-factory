"use client";

import {
  AllowlistEditor,
  BudgetForm,
  DiskQuotaForm,
  SettingsCard,
  ThresholdsForm,
} from "@/components/settings/forms";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-store";
import { effectiveCompanyStatus, type CompanyDetail } from "@/lib/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message || `HTTP ${err.status}`;
  if (err instanceof Error) return err.message;
  return "Unknown error";
}

export default function SettingsPage() {
  const params = useParams<{ id: string }>() ?? { id: "" };
  const cid = params.id;
  const qc = useQueryClient();
  const toast = useToast();
  const role = useAuth((s) => s.user?.role);

  const isAdmin = role === "admin";
  const canWrite = role === "admin" || role === "operator";

  const company = useQuery({
    queryKey: ["company", cid],
    queryFn: () => api<CompanyDetail>(`/companies/${cid}`),
  });

  const lifecycle = useMutation({
    mutationFn: (action: "pause" | "resume" | "close") =>
      api(`/companies/${cid}/${action}`, { method: "POST", json: {} }),
    onSuccess: (_data, action) => {
      qc.invalidateQueries({ queryKey: ["company", cid] });
      toast.success(`${action} succeeded`);
    },
    onError: (e) => toast.error("Operation failed", errorMessage(e)),
  });

  const c = company.data;
  const effective = effectiveCompanyStatus(c);

  return (
    <div className="space-y-4 p-6">
      <h1 className="text-lg font-semibold">Company settings</h1>

      <Card className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold">Lifecycle</h2>
          <p className="text-xs text-muted">
            Current status: {effective}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={!isAdmin || effective !== "active" || lifecycle.isPending}
            onClick={() => lifecycle.mutate("pause")}
          >
            Pause
          </Button>
          <Button
            variant="secondary"
            size="sm"
            disabled={!isAdmin || effective !== "paused" || lifecycle.isPending}
            onClick={() => lifecycle.mutate("resume")}
          >
            Resume
          </Button>
          <Button
            variant="danger"
            size="sm"
            disabled={!isAdmin || c?.status === "closed" || lifecycle.isPending}
            onClick={() => {
              if (confirm("Close the company? This action cannot be undone.")) {
                lifecycle.mutate("close");
              }
            }}
          >
            Close
          </Button>
        </div>
        {!isAdmin ? (
          <p className="text-[11px] text-muted">
            Admin access is required for these actions.
          </p>
        ) : null}
      </Card>

      <Tabs defaultValue="budget">
        <TabsList>
          <TabsTrigger value="budget">Budget</TabsTrigger>
          <TabsTrigger value="allowlist">Allowlist</TabsTrigger>
          <TabsTrigger value="thresholds">Auto-approve</TabsTrigger>
          <TabsTrigger value="disk">Disk quota</TabsTrigger>
        </TabsList>

        <TabsContent value="budget">
          <SettingsCard
            title="Budget"
            description="Update the total budget. Current spending and reserves are shown."
          >
            {canWrite ? <BudgetForm companyId={cid} /> : <ReadOnlyNotice />}
          </SettingsCard>
        </TabsContent>

        <TabsContent value="allowlist">
          <SettingsCard
            title="Allowlist"
            description="Service × action combinations available to this company."
          >
            <AllowlistEditor companyId={cid} readOnly={!canWrite} />
          </SettingsCard>
        </TabsContent>

        <TabsContent value="thresholds">
          <SettingsCard
            title="Auto-approve thresholds"
            description="Automatic approval for amounts below the threshold."
          >
            <ThresholdsForm companyId={cid} readOnly={!canWrite} />
          </SettingsCard>
        </TabsContent>

        <TabsContent value="disk">
          <SettingsCard
            title="Disk quota"
            description="Company total and per-agent quotas (MB)."
          >
            <DiskQuotaForm companyId={cid} readOnly={!canWrite} />
          </SettingsCard>
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ReadOnlyNotice() {
  return (
    <p className="text-xs text-muted">
      You don&apos;t have permission to edit this section.
    </p>
  );
}
