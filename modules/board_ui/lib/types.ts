/**
 * Shared API types — kept in sync with modules/board_api/schemas.py.
 * Only fields the UI actually consumes are listed.
 */

export type UserRole = "admin" | "operator" | "observer";

export interface User {
  id: string;
  email: string;
  role: UserRole;
  created_at: string;
  last_login: string | null;
}

export interface Token {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  refresh_expires_in: number;
}

export interface LoginResponse {
  user: User;
  token: Token;
}

export interface Company {
  id: string;
  name: string;
  status: string;
  created_at: string;
  closed_at: string | null;
}

export interface CompanyDetail extends Company {
  mission: string | null;
  industry: string | null;
  clock_state: string | null;
  company_time: string | null;
  real_time: string | null;
}

/**
 * Effective UI status for a company.
 *
 * The backend keeps two orthogonal concepts:
 *   - `status` — lifecycle (creating / active / closing / closed / failed)
 *   - `clock_state` — runtime pause (running / pausing / paused)
 *
 * For the operator's mental model we want a single label: an active company
 * whose clock is paused should read as "paused", not "active".
 */
export function effectiveCompanyStatus(
  c: Pick<CompanyDetail, "status" | "clock_state"> | null | undefined,
): string {
  if (!c) return "…";
  if (c.status === "active" && c.clock_state) {
    if (c.clock_state === "paused" || c.clock_state === "pausing") {
      return "paused";
    }
  }
  return c.status;
}

export interface CompanyCreateBody {
  name: string;
  mission: string;
  industry?: string | null;
  initial_budget_usd: string; // Decimal serialized
  company_disk_quota_mb: number;
  default_agent_quota_mb: number;
  auto_approve_threshold_usd?: string;
}

export interface Approval {
  request_id: string;
  company_id: string;
  kind: string;
  requester_id: string;
  payload: Record<string, unknown>;
  status: string;
  route_target: string;
  require_security: boolean;
  security_decision: string | null;
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  expires_at: string;
  created_at: string;
}

export interface Agent {
  id: string;
  role: string;
  persona_ref: string;
  reports_to: string | null;
  status: string;
  hired_at: string;
  first_name: string;
  last_name: string;
  role_title: string | null;
  role_description: string | null;
  fired_at: string | null;
  fired_by: string | null;
  fire_reason?: string | null;
}

export interface Feedback {
  id: string;
  target_agent_id: string;
  from_agent_id: string;
  rating: number;
  note: string;
  ts: string;
}

export interface PerformanceMetrics {
  tasks_completed: number;
  messages_sent: number;
  messages_received: number;
  tool_success_rate: number | null;
  avg_response_seconds: number | null;
  truncated_turns: number;
  health_alerts: number;
  tenure_days: number;
}

export interface PerformanceReport {
  metrics: PerformanceMetrics;
  recent_feedback: Feedback[];
}

export interface BudgetState {
  total_usd: string;
  spent_usd: string;
  reserved_usd: string;
  remaining_usd: string;
  blocked: boolean;
  by_category: Record<string, string>;
}

export interface BudgetPatch {
  total_usd: string;
}

export interface AllowlistEntry {
  service: string;
  action: string | null;
}

export interface AllowlistResponse {
  entries: AllowlistEntry[];
}

export interface AllowlistPatch {
  allow: AllowlistEntry[];
  revoke: AllowlistEntry[];
}

export interface ThresholdEntry {
  service: string;
  action: string;
  risk: string;
  auto_approve_threshold_usd: string | null;
}

export interface ThresholdsResponse {
  thresholds: ThresholdEntry[];
}

export interface ThresholdPatch {
  service: string;
  action: string;
  auto_approve_threshold_usd: string | null;
}

export interface DiskQuotaPatch {
  company_quota_mb?: number;
  agent_quota_mb?: Record<string, number>;
}

export interface ApiErrorBody {
  error?: string;
  code?: string;
  detail?: { error?: string; code?: string } | string;
}

export interface StreamEvent {
  id: number;
  kind: string;
  ts?: string;
  actor?: string | null;
  payload?: Record<string, unknown>;
  [k: string]: unknown;
}
