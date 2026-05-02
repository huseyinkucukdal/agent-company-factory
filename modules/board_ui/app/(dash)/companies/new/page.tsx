"use client";

import { Button } from "@/components/ui/button";
import { Input, Label, Textarea } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import type { Company, CompanyCreateBody } from "@/lib/types";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function NewCompanyPage() {
  const router = useRouter();
  const [form, setForm] = useState({
    name: "",
    mission: "",
    industry: "",
    initial_budget_usd: "1000",
    company_disk_quota_mb: 500,
    default_agent_quota_mb: 50,
    auto_approve_threshold_usd: "0",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function set<K extends keyof typeof form>(k: K, v: (typeof form)[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const body: CompanyCreateBody = {
        name: form.name,
        mission: form.mission,
        industry: form.industry || null,
        initial_budget_usd: form.initial_budget_usd,
        company_disk_quota_mb: Number(form.company_disk_quota_mb),
        default_agent_quota_mb: Number(form.default_agent_quota_mb),
        auto_approve_threshold_usd: form.auto_approve_threshold_usd,
      };
      const company = await api<Company>("/companies", {
        method: "POST",
        json: body,
      });
      router.replace(`/companies/${company.id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl p-6">
      <h1 className="mb-6 text-xl font-semibold">New company</h1>
      <form
        onSubmit={onSubmit}
        className="space-y-4 rounded-lg border bg-card p-6"
      >
        <div>
          <Label htmlFor="name">Ad</Label>
          <Input
            id="name"
            required
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="mission">Mission</Label>
          <Textarea
            id="mission"
            required
            value={form.mission}
            onChange={(e) => set("mission", e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="industry">Industry</Label>
          <Input
            id="industry"
            value={form.industry}
            onChange={(e) => set("industry", e.target.value)}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="budget">Budget (USD)</Label>
            <Input
              id="budget"
              required
              value={form.initial_budget_usd}
              onChange={(e) => set("initial_budget_usd", e.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="thresh">Auto-approve threshold (USD)</Label>
            <Input
              id="thresh"
              value={form.auto_approve_threshold_usd}
              onChange={(e) =>
                set("auto_approve_threshold_usd", e.target.value)
              }
            />
          </div>
          <div>
            <Label htmlFor="cdisk">Company disk quota (MB)</Label>
            <Input
              id="cdisk"
              type="number"
              min={1}
              value={form.company_disk_quota_mb}
              onChange={(e) =>
                set("company_disk_quota_mb", Number(e.target.value))
              }
            />
          </div>
          <div>
            <Label htmlFor="adisk">Per-agent quota (MB)</Label>
            <Input
              id="adisk"
              type="number"
              min={1}
              value={form.default_agent_quota_mb}
              onChange={(e) =>
                set("default_agent_quota_mb", Number(e.target.value))
              }
            />
          </div>
        </div>
        {error && (
          <div className="rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="secondary"
            onClick={() => router.back()}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create company"}
          </Button>
        </div>
      </form>
    </div>
  );
}
