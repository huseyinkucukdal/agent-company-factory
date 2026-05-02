/**
 * Convert raw `StreamEvent` rows into human-readable lines.
 *
 * The Board UI used to print `JSON.stringify(payload)` for every event,
 * which made the Live Feed unusable: every line was a wall of agent IDs
 * and request hashes. This module owns the mapping from event kind ->
 * one-line summary, plus the colour/icon hints the renderer uses.
 *
 * Keep this file flat and side-effect free: each formatter takes a
 * payload + an agent lookup and returns a `Humanized` row. Unknown
 * kinds fall back to a sensible default rather than throwing.
 */
import type { Agent, StreamEvent } from "./types";

export interface Humanized {
  icon: string;
  /** Compact, prefer-truncate-friendly summary line. */
  summary: string;
  /** Optional secondary detail (modal hint). */
  detail?: string;
  /** Tailwind class for the foreground colour of the summary. */
  tone: string;
  /**
   * Whether this row is "background noise" (heartbeats, delivered acks).
   * The renderer can dim it or hide it under a filter.
   */
  muted?: boolean;
}

export type AgentMap = Map<string, Agent>;

export function buildAgentMap(agents: Agent[]): AgentMap {
  const m = new Map<string, Agent>();
  for (const a of agents) m.set(a.id, a);
  return m;
}

/** "HR · 6912ac68" — never just the hash. */
export function agentLabel(id: string | null | undefined, agents: AgentMap): string {
  if (!id) return "system";
  if (id === "system") return "system";
  const a = agents.get(id);
  const short = id.slice(0, 8);
  if (a) return `${a.role.toUpperCase()} · ${short}`;
  return short;
}

const TONE_MUTED = "text-muted";
const TONE_INFO = "text-blue-600 dark:text-blue-300";
const TONE_ACT = "text-emerald-600 dark:text-emerald-300";
const TONE_WARN = "text-amber-600 dark:text-amber-300";
const TONE_DANGER = "text-red-600 dark:text-red-300";
const TONE_PURPLE = "text-purple-600 dark:text-purple-300";

function truncate(s: string, n = 90): string {
  if (!s) return "";
  const flat = s.replace(/\s+/g, " ").trim();
  return flat.length <= n ? flat : flat.slice(0, n - 1) + "…";
}

export function humanizeEvent(
  ev: StreamEvent,
  agents: AgentMap,
): Humanized {
  const p = (ev.payload ?? {}) as Record<string, unknown>;
  const actor = (ev as { actor_agent_id?: string }).actor_agent_id ?? null;

  switch (ev.kind) {
    case "company.created": {
      const name = String(p.name ?? "");
      return {
        icon: "🏢",
        summary: `Company created · ${name}`,
        tone: TONE_ACT,
      };
    }
    case "company.closed":
      return { icon: "🏚", summary: "Company closed", tone: TONE_WARN };

    case "agent.created": {
      const role = String(p.role ?? "?").toUpperCase();
      const id = String(p.agent_id ?? "");
      const mgr = p.manager_id ? agentLabel(String(p.manager_id), agents) : "—";
      return {
        icon: "🆕",
        summary: `Hired ${role} · ${id.slice(0, 8)}${
          mgr !== "—" ? ` (reports to ${mgr})` : " (no manager)"
        }`,
        tone: TONE_ACT,
      };
    }
    case "agent.fired": {
      const id = String(p.agent_id ?? actor ?? "");
      return {
        icon: "👋",
        summary: `Fired ${agentLabel(id, agents)}${
          p.reason ? `: ${truncate(String(p.reason), 60)}` : ""
        }`,
        tone: TONE_WARN,
      };
    }
    case "agent.hired":
      return {
        icon: "✅",
        summary: `Hire approved · ${agentLabel(String(p.agent_id ?? ""), agents)}`,
        tone: TONE_ACT,
      };
    case "agent.fire_notify":
      return {
        icon: "📣",
        summary: `Fire notification · ${agentLabel(String(p.agent_id ?? ""), agents)}`,
        tone: TONE_MUTED,
        muted: true,
      };
    case "agent.orphaned":
      return {
        icon: "🧭",
        summary: `Orphaned · ${agentLabel(String(p.agent_id ?? ""), agents)}`,
        tone: TONE_WARN,
      };
    case "agent.heartbeat": {
      const state = String(p.state ?? "?");
      return {
        icon: "💓",
        summary: `${agentLabel(String(p.agent_id ?? actor), agents)} ${state}`,
        tone: TONE_MUTED,
        muted: true,
      };
    }
    case "agent.health_alert": {
      const issue = String(p.issue ?? "alert");
      const sev = String(p.severity ?? "info");
      return {
        icon: sev === "critical" ? "🔴" : sev === "warn" ? "🟡" : "ℹ️",
        summary: `${agentLabel(String(p.agent_id ?? ""), agents)} · ${issue.replace(/_/g, " ")}`,
        detail: p.detail ? truncate(String(p.detail), 200) : undefined,
        tone: sev === "critical" ? TONE_DANGER : TONE_WARN,
      };
    }
    case "agent.turn_truncated": {
      const calls = Number(p.tool_invocations ?? 0);
      return {
        icon: "✂️",
        summary: `${agentLabel(String(p.agent_id ?? ""), agents)} · turn cut off after ${calls} tool calls`,
        detail: `LLM stop_reason=${String(p.reason ?? "tool_loop_cap")}`,
        tone: TONE_WARN,
      };
    }

    case "message.sent": {
      const from = agentLabel(String(p.from_agent ?? actor), agents);
      const to = agentLabel(String(p.to_agent ?? ""), agents);
      const body = truncate(String(p.content ?? ""), 110);
      return {
        icon: "💬",
        summary: `${from} → ${to}: ${body}`,
        detail: String(p.content ?? ""),
        tone: TONE_INFO,
      };
    }
    case "message.delivered": {
      const to = agentLabel(String(p.to_agent ?? ""), agents);
      return {
        icon: "✓",
        summary: `delivered → ${to}`,
        tone: TONE_MUTED,
        muted: true,
      };
    }

    case "tool.called": {
      const tool = String(p.tool ?? "?");
      const args = (p.arguments ?? {}) as Record<string, unknown>;
      const argHint = summariseArgs(tool, args);
      return {
        icon: "🔧",
        summary: `${agentLabel(actor, agents)} · ${tool}${argHint ? `(${argHint})` : "()"}`,
        tone: TONE_ACT,
      };
    }
    case "tool.result": {
      const tool = String(p.tool ?? "?");
      const ok = Boolean(p.ok);
      if (ok) {
        const result = (p.result ?? {}) as Record<string, unknown>;
        const hint = summariseResult(tool, result);
        return {
          icon: "✓",
          summary: `${tool}${hint ? ` · ${hint}` : " · ok"}`,
          tone: TONE_MUTED,
          muted: true,
        };
      }
      return {
        icon: "✗",
        summary: `${tool} failed: ${truncate(String(p.error ?? ""), 80)}`,
        tone: TONE_DANGER,
      };
    }
    case "tool.denied": {
      const tool = String(p.tool ?? "?");
      return {
        icon: "🚫",
        summary: `${agentLabel(actor, agents)} · ${tool} denied${
          p.reason ? `: ${truncate(String(p.reason), 60)}` : ""
        }`,
        tone: TONE_DANGER,
      };
    }

    case "expense.charged": {
      const cents = Number(p.amount_cents ?? 0);
      const cat = String(p.category ?? "other");
      const dollars = (cents / 100).toFixed(2);
      return {
        icon: "💰",
        summary: `$${dollars} · ${cat}${p.note ? ` · ${truncate(String(p.note), 50)}` : ""}`,
        tone: cents > 0 ? TONE_WARN : TONE_MUTED,
        muted: cents === 0,
      };
    }
    case "budget.warning":
      return { icon: "⚠️", summary: "Budget warning", tone: TONE_WARN };
    case "budget.blocked":
      return { icon: "⛔", summary: "Budget blocked — out of funds", tone: TONE_DANGER };

    case "approval.requested": {
      const kind = String(p.kind ?? "?");
      return {
        icon: "📝",
        summary: `Approval requested · ${kind}`,
        tone: TONE_PURPLE,
      };
    }
    case "approval.decided": {
      const decision = String(p.decision ?? p.status ?? "?");
      const tone = decision === "approved" ? TONE_ACT : TONE_DANGER;
      return {
        icon: decision === "approved" ? "✅" : "🛑",
        summary: `Approval ${decision}${p.kind ? ` · ${String(p.kind)}` : ""}`,
        tone,
      };
    }
    case "approval.timeout":
      return {
        icon: "⏱",
        summary: `Approval timed out${p.kind ? ` · ${String(p.kind)}` : ""}`,
        tone: TONE_WARN,
      };

    case "security.flag":
      return {
        icon: "⚠️",
        summary: `Security flag${p.note ? `: ${truncate(String(p.note), 80)}` : ""}`,
        tone: TONE_DANGER,
      };
    case "security.veto":
      return {
        icon: "🛑",
        summary: `Security veto${p.note ? `: ${truncate(String(p.note), 80)}` : ""}`,
        tone: TONE_DANGER,
      };

    case "connector.external_call":
      return {
        icon: "🌐",
        summary: `External call · ${String(p.service ?? "?")}/${String(p.action ?? "?")}`,
        tone: TONE_INFO,
      };

    case "quota.warning":
      return { icon: "📦", summary: "Quota warning", tone: TONE_WARN };
    case "quota.exceeded":
      return { icon: "📦", summary: "Quota exceeded", tone: TONE_DANGER };
    case "quota.drift":
      return {
        icon: "📦",
        summary: "Quota drift detected",
        tone: TONE_MUTED,
        muted: true,
      };

    case "clock.paused":
      return { icon: "⏸", summary: "Clock paused", tone: TONE_WARN };
    case "clock.resumed":
      return { icon: "▶", summary: "Clock resumed", tone: TONE_INFO };
    case "clock.day_tick":
      return {
        icon: "🌓",
        summary: `Day tick${p.day ? ` · day ${String(p.day)}` : ""}`,
        tone: TONE_MUTED,
        muted: true,
      };

    case "link.requested":
      return { icon: "🔗", summary: "Inter-company link requested", tone: TONE_PURPLE };
    case "link.approved":
      return { icon: "🔗", summary: "Inter-company link approved", tone: TONE_ACT };

    case "efficiency.finding.opened":
      return {
        icon: "📊",
        summary: `Efficiency finding opened${p.detector ? ` · ${String(p.detector)}` : ""}`,
        tone: TONE_WARN,
      };
    case "efficiency.finding.closed":
      return {
        icon: "📊",
        summary: "Efficiency finding closed",
        tone: TONE_MUTED,
        muted: true,
      };

    default: {
      const summary = ev.payload
        ? truncate(JSON.stringify(ev.payload), 110)
        : "";
      return {
        icon: "·",
        summary: summary || "(no payload)",
        tone: TONE_MUTED,
        muted: true,
      };
    }
  }
}

function summariseArgs(tool: string, args: Record<string, unknown>): string {
  switch (tool) {
    case "send_message": {
      const recipient = String(args.recipient_id ?? "");
      const body = truncate(String(args.body ?? ""), 60);
      return `to=${recipient.slice(0, 8)}, body="${body}"`;
    }
    case "write_my_workspace":
    case "read_my_workspace":
    case "list_my_workspace":
      return args.path ? String(args.path) : "";
    case "propose_hire":
      return args.role ? `role=${String(args.role)}` : "";
    case "propose_fire":
      return args.target_id ? `target=${String(args.target_id).slice(0, 8)}` : "";
    case "request_approval":
      return args.kind ? `kind=${String(args.kind)}` : "";
    default: {
      const flat = JSON.stringify(args);
      return flat.length > 60 ? flat.slice(0, 59) + "…" : flat;
    }
  }
}

function summariseResult(tool: string, result: Record<string, unknown>): string {
  switch (tool) {
    case "write_my_workspace":
      return result.bytes_written
        ? `${String(result.bytes_written)} bytes written`
        : "";
    case "read_my_workspace":
      return result.bytes ? `${String(result.bytes)} bytes` : "";
    case "send_message":
      return result.message_id ? `msg=${String(result.message_id).slice(0, 12)}` : "";
    default:
      return "";
  }
}
