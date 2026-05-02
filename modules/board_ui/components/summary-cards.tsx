"use client";

import { api } from "@/lib/api";
import type { Agent, Approval, BudgetState, StreamEvent } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";

interface LLMStats {
  total: number;
  failed: number;
  rate_limited: number;
  last_provider: string | null;
  last_model: string | null;
  last_at: string | null;
}

interface SummaryCardsProps {
  companyId: string;
}

const REFETCH_MS = 5_000;

export function SummaryCards({ companyId }: SummaryCardsProps) {
  const agents = useQuery({
    queryKey: ["company", companyId, "agents"],
    queryFn: () => api<Agent[]>(`/companies/${companyId}/agents`),
    refetchInterval: REFETCH_MS,
  });
  const budget = useQuery({
    queryKey: ["company", companyId, "budget"],
    queryFn: () => api<BudgetState>(`/companies/${companyId}/budget`),
    refetchInterval: REFETCH_MS,
  });
  const llm = useQuery({
    queryKey: ["company", companyId, "llm-stats"],
    queryFn: () => api<LLMStats>(`/companies/${companyId}/llm/stats`),
    refetchInterval: REFETCH_MS,
  });
  const approvals = useQuery({
    queryKey: ["company", companyId, "approvals-pending"],
    queryFn: () =>
      api<Approval[]>(`/companies/${companyId}/approvals?status=pending`),
    refetchInterval: REFETCH_MS,
  });
  const truncated = useQuery({
    queryKey: ["company", companyId, "truncated"],
    queryFn: () =>
      api<StreamEvent[]>(
        `/companies/${companyId}/events?kinds=agent.turn_truncated&limit=50`,
      ),
    refetchInterval: REFETCH_MS,
  });

  const activeAgents = (agents.data ?? []).filter((a) => a.status === "active");
  const roleCounts = countRoles(activeAgents);

  const truncatedRecent = recentlyTruncated(truncated.data ?? [], 60 * 60_000);

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      <Card
        label="Team"
        value={activeAgents.length.toString()}
        sub={
          activeAgents.length > 0
            ? formatRoleSummary(roleCounts)
            : "no active agents"
        }
        tone="info"
      />
      <Card
        label="Budget"
        value={budget.data ? `$${trim(budget.data.remaining_usd)}` : "—"}
        sub={budget.data ? `of $${trim(budget.data.total_usd)} total` : ""}
        tone={budgetTone(budget.data)}
      />
      <Card
        label="LLM calls"
        value={llm.data ? llm.data.total.toLocaleString() : "—"}
        sub={
          llm.data
            ? llm.data.last_at
              ? `${llm.data.last_provider ?? "?"} · ${llm.data.last_model ?? "?"} · ${relTime(llm.data.last_at)}`
              : "no calls yet"
            : ""
        }
        tone={llm.data && llm.data.failed > 0 ? "warn" : "info"}
        footer={
          llm.data && (llm.data.failed > 0 || llm.data.rate_limited > 0)
            ? `${llm.data.failed} failed · ${llm.data.rate_limited} throttled`
            : undefined
        }
      />
      <Card
        label="Approvals"
        value={(approvals.data?.length ?? 0).toString()}
        sub={
          approvals.data && approvals.data.length > 0
            ? "pending decision"
            : "all clear"
        }
        tone={approvals.data && approvals.data.length > 0 ? "warn" : "info"}
      />
      <Card
        label="Cut-off turns"
        value={truncatedRecent.toString()}
        sub={
          truncatedRecent > 0
            ? "in last hour · raise LLM_MAX_TOOL_LOOPS"
            : "none in the last hour"
        }
        tone={truncatedRecent > 0 ? "warn" : "info"}
      />
    </div>
  );
}

interface CardProps {
  label: string;
  value: string;
  sub: string;
  footer?: string;
  tone: "info" | "warn" | "danger";
}

function Card({ label, value, sub, footer, tone }: CardProps) {
  const accent =
    tone === "danger"
      ? "text-red-600 dark:text-red-300"
      : tone === "warn"
        ? "text-amber-600 dark:text-amber-300"
        : "text-fg";
  return (
    <div className="rounded-lg border bg-card p-3 shadow-sm">
      <div className="text-[10px] uppercase tracking-wide text-muted">
        {label}
      </div>
      <div className={"mt-1 text-2xl font-semibold tabular-nums " + accent}>
        {value}
      </div>
      <div className="mt-0.5 truncate text-xs text-muted">{sub}</div>
      {footer ? (
        <div className="mt-1 truncate text-[10px] text-muted">{footer}</div>
      ) : null}
    </div>
  );
}

function countRoles(agents: Agent[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const a of agents) {
    const r = a.role.toUpperCase();
    out[r] = (out[r] ?? 0) + 1;
  }
  return out;
}

function formatRoleSummary(counts: Record<string, number>): string {
  const parts: string[] = [];
  for (const [role, n] of Object.entries(counts)) {
    parts.push(n > 1 ? `${role}×${n}` : role);
  }
  return parts.join(" · ");
}

function trim(decimal: string): string {
  // "10.0000" → "10.00", "8.5" → "8.50"
  const n = Number(decimal);
  if (Number.isNaN(n)) return decimal;
  return n.toFixed(2);
}

function budgetTone(b: BudgetState | undefined): "info" | "warn" | "danger" {
  if (!b) return "info";
  if (b.blocked) return "danger";
  const total = Number(b.total_usd);
  const remaining = Number(b.remaining_usd);
  if (!total || Number.isNaN(total) || Number.isNaN(remaining)) return "info";
  const pct = remaining / total;
  if (pct < 0.1) return "danger";
  if (pct < 0.3) return "warn";
  return "info";
}

function relTime(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const dt = Date.now() - t;
  if (dt < 5_000) return "just now";
  if (dt < 60_000) return `${Math.floor(dt / 1000)}s ago`;
  if (dt < 3600_000) return `${Math.floor(dt / 60_000)}m ago`;
  if (dt < 86_400_000) return `${Math.floor(dt / 3600_000)}h ago`;
  return `${Math.floor(dt / 86_400_000)}d ago`;
}

function recentlyTruncated(events: StreamEvent[], windowMs: number): number {
  const now = Date.now();
  let count = 0;
  for (const e of events) {
    const ts =
      (e as { ts_real?: string; ts?: string }).ts_real ??
      (e as { ts?: string }).ts ??
      null;
    if (!ts) {
      count += 1;
      continue;
    }
    const t = Date.parse(ts);
    if (Number.isNaN(t) || now - t <= windowMs) count += 1;
  }
  return count;
}
