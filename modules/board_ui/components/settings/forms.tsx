"use client";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { api, ApiError } from "@/lib/api";
import type {
  AllowlistEntry,
  AllowlistPatch,
  AllowlistResponse,
  Agent,
  BudgetPatch,
  BudgetState,
  DiskQuotaPatch,
  ThresholdEntry,
  ThresholdPatch,
  ThresholdsResponse,
} from "@/lib/types";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

// ---------------------------------------------------------------- shell

export function SettingsCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="space-y-3">
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        {description ? (
          <p className="text-xs text-muted">{description}</p>
        ) : null}
      </div>
      {children}
    </Card>
  );
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message || `HTTP ${err.status}`;
  if (err instanceof Error) return err.message;
  return "Unknown error";
}

// ---------------------------------------------------------------- budget

const budgetSchema = z.object({
  total_usd: z
    .string()
    .min(1, "Required")
    .refine((v) => Number(v) > 0, "Must be greater than 0"),
});

export function BudgetForm({ companyId }: { companyId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading } = useQuery({
    queryKey: ["budget", companyId],
    queryFn: () => api<BudgetState>(`/companies/${companyId}/budget`),
  });
  const form = useForm<BudgetPatch>({
    resolver: zodResolver(budgetSchema),
    values: data ? { total_usd: data.total_usd } : { total_usd: "" },
  });
  const mutate = useMutation({
    mutationFn: (body: BudgetPatch) =>
      api<BudgetState>(`/companies/${companyId}/budget`, {
        method: "PATCH",
        json: body,
      }),
    onSuccess: (next) => {
      qc.setQueryData(["budget", companyId], next);
      toast.success("Budget updated");
    },
    onError: (e) => toast.error("Failed to update budget", errorMessage(e)),
  });

  if (isLoading) return <div className="text-xs text-muted">Loading…</div>;

  return (
    <form
      className="space-y-3"
      onSubmit={form.handleSubmit((v) => mutate.mutate(v))}
    >
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label>Total budget (USD)</Label>
          <Input
            inputMode="decimal"
            placeholder="1000.00"
            {...form.register("total_usd")}
          />
          {form.formState.errors.total_usd ? (
            <p className="mt-1 text-xs text-red-600">
              {form.formState.errors.total_usd.message}
            </p>
          ) : null}
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs text-muted">
          <Stat label="Harcanan" value={data?.spent_usd} />
          <Stat label="Rezerve" value={data?.reserved_usd} />
          <Stat label="Kalan" value={data?.remaining_usd} />
        </div>
      </div>
      <div className="flex justify-end">
        <Button type="submit" disabled={mutate.isPending}>
          {mutate.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </form>
  );
}

function Stat({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded-md border bg-bg p-2">
      <div className="text-[10px] uppercase tracking-wide">{label}</div>
      <div className="font-mono text-sm text-fg">{value ?? "—"}</div>
    </div>
  );
}

// ---------------------------------------------------------------- allowlist

export function AllowlistEditor({
  companyId,
  readOnly = false,
}: {
  companyId: string;
  readOnly?: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading } = useQuery({
    queryKey: ["allowlist", companyId],
    queryFn: () =>
      api<AllowlistResponse>(`/companies/${companyId}/allowlist`),
  });
  const thresholds = useQuery({
    queryKey: ["thresholds", companyId],
    queryFn: () =>
      api<ThresholdsResponse>(
        `/companies/${companyId}/auto_approve_thresholds`,
      ),
  });

  // Build a (service, action) universe by unioning the allowlist with the
  // catalog of known thresholds (which exposes every registered action).
  const known = useMemo<AllowlistEntry[]>(() => {
    const seen = new Set<string>();
    const out: AllowlistEntry[] = [];
    const push = (e: AllowlistEntry) => {
      const k = `${e.service}::${e.action ?? "*"}`;
      if (seen.has(k)) return;
      seen.add(k);
      out.push(e);
    };
    for (const e of data?.entries ?? []) push(e);
    for (const t of thresholds.data?.thresholds ?? []) {
      push({ service: t.service, action: t.action });
    }
    out.sort((a, b) =>
      a.service === b.service
        ? (a.action ?? "").localeCompare(b.action ?? "")
        : a.service.localeCompare(b.service),
    );
    return out;
  }, [data, thresholds.data]);

  const allowed = useMemo(() => {
    const set = new Set<string>();
    for (const e of data?.entries ?? []) {
      set.add(`${e.service}::${e.action ?? "*"}`);
    }
    return set;
  }, [data]);

  const mutate = useMutation({
    mutationFn: (body: AllowlistPatch) =>
      api<AllowlistResponse>(`/companies/${companyId}/allowlist`, {
        method: "PATCH",
        json: body,
      }),
    onSuccess: (next) => {
      qc.setQueryData(["allowlist", companyId], next);
      toast.success("Allowlist updated");
    },
    onError: (e) => toast.error("Failed to update allowlist", errorMessage(e)),
  });

  if (isLoading) return <div className="text-xs text-muted">Loading…</div>;

  const onToggle = (entry: AllowlistEntry, checked: boolean) => {
    if (readOnly) return;
    const body: AllowlistPatch = { allow: [], revoke: [] };
    if (checked) body.allow.push(entry);
    else body.revoke.push(entry);
    mutate.mutate(body);
  };

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted">
        Manage allowed service × action combinations. Unchecked
        combinations are blocked.
      </div>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-bg text-left text-xs text-muted">
            <tr>
              <th className="px-3 py-2">Service</th>
              <th className="px-3 py-2">Action</th>
              <th className="px-3 py-2 text-center">Allowed</th>
            </tr>
          </thead>
          <tbody>
            {known.map((e) => {
              const k = `${e.service}::${e.action ?? "*"}`;
              const isOn = allowed.has(k);
              return (
                <tr key={k} className="border-t">
                  <td className="px-3 py-2 font-mono text-xs">{e.service}</td>
                  <td className="px-3 py-2 font-mono text-xs">
                    {e.action ?? "*"}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <input
                      type="checkbox"
                      checked={isOn}
                      disabled={readOnly || mutate.isPending}
                      onChange={(ev) => onToggle(e, ev.target.checked)}
                    />
                  </td>
                </tr>
              );
            })}
            {known.length === 0 ? (
              <tr>
                <td colSpan={3} className="px-3 py-4 text-center text-muted">
                  No known services.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- thresholds

export function ThresholdsForm({
  companyId,
  readOnly = false,
}: {
  companyId: string;
  readOnly?: boolean;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading } = useQuery({
    queryKey: ["thresholds", companyId],
    queryFn: () =>
      api<ThresholdsResponse>(
        `/companies/${companyId}/auto_approve_thresholds`,
      ),
  });

  const mutate = useMutation({
    mutationFn: (body: ThresholdPatch) =>
      api<ThresholdsResponse>(
        `/companies/${companyId}/auto_approve_thresholds`,
        { method: "PATCH", json: body },
      ),
    onSuccess: (next) => {
      qc.setQueryData(["thresholds", companyId], next);
      toast.success("Threshold updated");
    },
    onError: (e) => toast.error("Failed to update threshold", errorMessage(e)),
  });

  if (isLoading) return <div className="text-xs text-muted">Loading…</div>;

  return (
    <div className="space-y-3">
      <div className="text-xs text-muted">
        Auto-approval threshold per service × action. Leave empty to disable auto-approve;
        amounts below the threshold are auto-approved.
      </div>
      <div className="overflow-x-auto rounded-md border">
        <table className="w-full text-sm">
          <thead className="bg-bg text-left text-xs text-muted">
            <tr>
              <th className="px-3 py-2">Service</th>
              <th className="px-3 py-2">Action</th>
              <th className="px-3 py-2">Risk</th>
              <th className="px-3 py-2">Threshold (USD)</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {(data?.thresholds ?? []).map((t) => (
              <ThresholdRow
                key={`${t.service}::${t.action}`}
                entry={t}
                disabled={readOnly || mutate.isPending}
                onSave={(value) =>
                  mutate.mutate({
                    service: t.service,
                    action: t.action,
                    auto_approve_threshold_usd: value,
                  })
                }
              />
            ))}
            {!data?.thresholds.length ? (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-center text-muted">
                  No thresholds defined.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ThresholdRow({
  entry,
  disabled,
  onSave,
}: {
  entry: ThresholdEntry;
  disabled: boolean;
  onSave: (value: string | null) => void;
}) {
  const [draft, setDraft] = useState(entry.auto_approve_threshold_usd ?? "");
  const dirty = draft !== (entry.auto_approve_threshold_usd ?? "");
  return (
    <tr className="border-t">
      <td className="px-3 py-2 font-mono text-xs">{entry.service}</td>
      <td className="px-3 py-2 font-mono text-xs">{entry.action}</td>
      <td className="px-3 py-2 text-xs">{entry.risk}</td>
      <td className="px-3 py-2">
        <Input
          inputMode="decimal"
          value={draft}
          placeholder="off"
          disabled={disabled}
          onChange={(ev) => setDraft(ev.target.value)}
          className="h-8"
        />
      </td>
      <td className="px-3 py-2 text-right">
        <Button
          size="sm"
          variant="secondary"
          disabled={disabled || !dirty}
          onClick={() => {
            const val = draft.trim();
            if (val !== "" && !(Number(val) >= 0)) return;
            onSave(val === "" ? null : val);
          }}
        >
          Save
        </Button>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------- disk quota

const diskCompanySchema = z.object({
  company_quota_mb: z
    .string()
    .optional()
    .refine(
      (v) => v == null || v === "" || Number(v) > 0,
      "Must be greater than 0",
    ),
});

export function DiskQuotaForm({
  companyId,
  readOnly = false,
}: {
  companyId: string;
  readOnly?: boolean;
}) {
  const toast = useToast();
  const agents = useQuery({
    queryKey: ["company", companyId, "agents"],
    queryFn: () => api<Agent[]>(`/companies/${companyId}/agents`),
  });

  const companyForm = useForm<{ company_quota_mb: string }>({
    resolver: zodResolver(diskCompanySchema),
    defaultValues: { company_quota_mb: "" },
  });

  const mutate = useMutation({
    mutationFn: (body: DiskQuotaPatch) =>
      api(`/companies/${companyId}/disk_quota`, {
        method: "PATCH",
        json: body,
      }),
    onSuccess: () => toast.success("Disk quota updated"),
    onError: (e) => toast.error("Failed to update disk quota", errorMessage(e)),
  });

  const [agentDrafts, setAgentDrafts] = useState<Record<string, string>>({});

  return (
    <div className="space-y-4">
      <form
        className="space-y-3"
        onSubmit={companyForm.handleSubmit((v) => {
          const body: DiskQuotaPatch = {};
          if (v.company_quota_mb && v.company_quota_mb.trim() !== "") {
            body.company_quota_mb = Number(v.company_quota_mb);
          }
          if (Object.keys(body).length === 0) return;
          mutate.mutate(body);
        })}
      >
        <div>
          <Label>Company total quota (MB)</Label>
          <Input
            inputMode="numeric"
            placeholder="e.g. 2048"
            disabled={readOnly}
            {...companyForm.register("company_quota_mb")}
          />
          {companyForm.formState.errors.company_quota_mb ? (
            <p className="mt-1 text-xs text-red-600">
              {companyForm.formState.errors.company_quota_mb.message}
            </p>
          ) : null}
        </div>
        <div className="flex justify-end">
          <Button
            type="submit"
            disabled={readOnly || mutate.isPending}
            variant="secondary"
          >
            Save company quota
          </Button>
        </div>
      </form>

      <div>
        <div className="mb-2 text-xs font-medium text-muted">
          Per-agent quota (MB)
        </div>
        <div className="overflow-x-auto rounded-md border">
          <table className="w-full text-sm">
            <thead className="bg-bg text-left text-xs text-muted">
              <tr>
              <th className="px-3 py-2">Role</th>
              <th className="px-3 py-2">Agent</th>
              <th className="px-3 py-2">Quota (MB)</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {(agents.data ?? []).map((a) => {
                const draft = agentDrafts[a.id] ?? "";
                return (
                  <tr key={a.id} className="border-t">
                    <td className="px-3 py-2 text-xs">{a.role}</td>
                    <td className="px-3 py-2 font-mono text-[10px]">{a.id}</td>
                    <td className="px-3 py-2">
                      <Input
                        inputMode="numeric"
                        className="h-8"
                        value={draft}
                        disabled={readOnly}
                        placeholder="—"
                        onChange={(ev) =>
                          setAgentDrafts((prev) => ({
                            ...prev,
                            [a.id]: ev.target.value,
                          }))
                        }
                      />
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={readOnly || !draft || mutate.isPending}
                        onClick={() => {
                          const mb = Number(draft);
                          if (!(mb > 0)) return;
                          mutate.mutate({ agent_quota_mb: { [a.id]: mb } });
                        }}
                      >
                        Save
                      </Button>
                    </td>
                  </tr>
                );
              })}
              {!agents.data?.length ? (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center text-muted">
                    No agents.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
