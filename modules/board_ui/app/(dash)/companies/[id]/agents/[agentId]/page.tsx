"use client";

import { AgentConversations } from "@/components/agent-conversations";
import { Card, StatusPill } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input, Label, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { api } from "@/lib/api";
import { agentLabel, buildAgentMap } from "@/lib/humanize-event";
import type { Agent, PerformanceReport } from "@/lib/types";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

interface FileMeta {
  relative_path: string;
  size_bytes: number;
  modified_at: string;
  is_dir?: boolean;
}
interface WorkspaceListing {
  agent_id: string;
  files: FileMeta[];
}
interface WorkspaceFile {
  agent_id: string;
  relative_path: string;
  content: string;
  size_bytes: number;
}
type WorkspaceSelection = {
  scope: "agent" | "project";
  path: string;
};

export default function AgentDetailPage() {
  const params = useParams<{ id: string; agentId: string }>() ?? { id: "", agentId: "" };
  const { id: cid, agentId } = params;
  const toast = useToast();
  const queryClient = useQueryClient();
  const agent = useQuery({
    queryKey: ["company", cid, "agent", agentId],
    queryFn: () => api<Agent>(`/companies/${cid}/agents/${agentId}`),
  });
  const allAgents = useQuery({
    queryKey: ["company", cid, "agents"],
    queryFn: () => api<Agent[]>(`/companies/${cid}/agents`),
  });
  const agentMap = useMemo(
    () => buildAgentMap(allAgents.data ?? []),
    [allAgents.data],
  );
  const performance = useQuery({
    queryKey: ["company", cid, "agent", agentId, "performance"],
    queryFn: () =>
      api<PerformanceReport>(`/companies/${cid}/agents/${agentId}/performance`),
    refetchInterval: 10_000,
  });
  const ws = useQuery({
    queryKey: ["company", cid, "agent", agentId, "workspace"],
    queryFn: () =>
      api<WorkspaceListing>(`/companies/${cid}/agents/${agentId}/workspace`),
    retry: false,
  });
  const projectWs = useQuery({
    queryKey: ["company", cid, "project-workspace"],
    queryFn: () => api<WorkspaceListing>(`/companies/${cid}/project-workspace`),
    retry: false,
  });
  const [selectedFile, setSelectedFile] = useState<WorkspaceSelection | null>(null);
  const fileContent = useQuery({
    queryKey: [
      "company",
      cid,
      selectedFile?.scope,
      "workspace-file",
      selectedFile?.path,
    ],
    queryFn: () => {
      const path = encodeURIComponent(selectedFile?.path ?? "");
      const endpoint =
        selectedFile?.scope === "project"
          ? `/companies/${cid}/project-workspace/file?path=${path}`
          : `/companies/${cid}/agents/${agentId}/workspace/file?path=${path}`;
      return api<WorkspaceFile>(endpoint);
    },
    enabled: Boolean(selectedFile),
    retry: false,
  });

  const invalidatePerformance = () =>
    queryClient.invalidateQueries({
      queryKey: ["company", cid, "agent", agentId, "performance"],
    });

  const sendReport = useMutation({
    mutationFn: () =>
      api<{ delivered_to: string }>(
        `/companies/${cid}/agents/${agentId}/performance/report`,
        { method: "POST" },
      ),
    onSuccess: (data) => toast.success("Report sent", `Delivered to ${data.delivered_to}`),
    onError: (err) => toast.error("Report failed", (err as Error).message),
  });

  const displayTitle =
    agent.data?.role_title || agent.data?.role || "…";
  const displayName =
    [agent.data?.first_name, agent.data?.last_name].filter(Boolean).join(" ") ||
    agentId;
  const managerId = agent.data?.reports_to ?? null;

  return (
    <div className="space-y-4 p-6">
      <Card>
        <div className="flex items-center justify-between">
          <div>
            <div className="text-xs text-muted">Agent</div>
            <h1 className="text-lg font-semibold">{displayTitle}</h1>
            <div className="text-sm text-muted">{displayName}</div>
            <div className="font-mono text-[11px] text-muted">{agentId}</div>
          </div>
          <StatusPill status={agent.data?.status ?? "…"} />
        </div>
        {agent.data?.persona_ref && (
          <div className="mt-3 text-sm">
            <span className="text-muted">Persona: </span>
            <span className="font-mono">{agent.data.persona_ref}</span>
          </div>
        )}
        {agent.data?.role_description ? (
          <div className="mt-3 max-w-3xl text-sm text-muted">
            {agent.data.role_description}
          </div>
        ) : null}
      </Card>

      {agent.data?.status === "fired" || agent.data?.fired_at ? (
        <FireRecordCard
          firedAt={agent.data.fired_at ?? null}
          firedBy={agent.data.fired_by ?? null}
          fireReason={agent.data.fire_reason ?? null}
          firedByLabel={
            agent.data.fired_by
              ? agentLabel(agent.data.fired_by, agentMap)
              : null
          }
        />
      ) : null}

      <Card>
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-sm font-medium">Performance</div>
            {managerId ? (
              <div className="mt-1 text-xs text-muted">
                Manager: <span className="font-mono">{managerId}</span>
              </div>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="secondary"
              onClick={() => sendReport.mutate()}
              disabled={!managerId || sendReport.isPending}
            >
              Send report
            </Button>
            {managerId ? (
              <>
                <FeedbackDialog
                  companyId={cid}
                  agentId={agentId}
                  managerId={managerId}
                  onDone={invalidatePerformance}
                />
                <RoleChangeDialog
                  companyId={cid}
                  agentId={agentId}
                  managerId={managerId}
                  currentTitle={displayTitle}
                  currentDescription={agent.data?.role_description ?? ""}
                  onDone={() => {
                    queryClient.invalidateQueries({
                      queryKey: ["company", cid, "agent", agentId],
                    });
                    invalidatePerformance();
                  }}
                />
                <FireDialog
                  companyId={cid}
                  agentId={agentId}
                  managerId={managerId}
                  onDone={() => {
                    queryClient.invalidateQueries({
                      queryKey: ["company", cid, "agent", agentId],
                    });
                    queryClient.invalidateQueries({
                      queryKey: ["company", cid, "agents"],
                    });
                  }}
                />
              </>
            ) : null}
          </div>
        </div>
        {performance.error ? (
          <div className="text-xs text-muted">
            Performance unavailable: {(performance.error as Error).message}
          </div>
        ) : (
          <>
            <MetricGrid metrics={performance.data?.metrics} />
            <div className="mt-4">
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
                Recent feedback
              </div>
              {performance.data?.recent_feedback.length ? (
                <div className="divide-y rounded-md border">
                  {performance.data.recent_feedback.map((item) => (
                    <div key={item.id} className="p-3 text-sm">
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-medium">{item.rating}/5</div>
                        <div className="text-xs text-muted">
                          {new Date(item.ts).toLocaleString()}
                        </div>
                      </div>
                      <div className="mt-1 text-muted">{item.note}</div>
                      <div className="mt-1 font-mono text-[10px] text-muted">
                        from {item.from_agent_id}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-md border p-3 text-sm text-muted">
                  No feedback yet.
                </div>
              )}
            </div>
          </>
        )}
      </Card>

      <Card>
        <div className="mb-3">
          <div className="text-sm font-medium">Conversations</div>
          <div className="text-xs text-muted">
            Every message this agent sent or received, in order.
          </div>
        </div>
        <AgentConversations
          companyId={cid}
          agentId={agentId}
          agents={allAgents.data ?? []}
        />
      </Card>

      <WorkspaceCard
        title="Project Workspace"
        description="Shared product files for the whole company."
        listing={projectWs.data}
        error={projectWs.error}
        onOpen={(path) => setSelectedFile({ scope: "project", path })}
      />

      <WorkspaceCard
        title="Personal Workspace"
        description="Private files owned by this agent."
        listing={ws.data}
        error={ws.error}
        onOpen={(path) => setSelectedFile({ scope: "agent", path })}
      />

      <Dialog
        open={selectedFile !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedFile(null);
        }}
      >
        <DialogContent
          title={
            selectedFile
              ? `${selectedFile.scope === "project" ? "Project" : "Personal"}: ${selectedFile.path}`
              : "Workspace file"
          }
          className="w-[min(92vw,860px)]"
        >
          {fileContent.isLoading ? (
            <div className="rounded-md border p-3 text-sm text-muted">
              Loading file…
            </div>
          ) : fileContent.error ? (
            <div className="rounded-md border border-red-500/40 p-3 text-sm text-red-600">
              {(fileContent.error as Error).message}
            </div>
          ) : (
            <div className="space-y-3">
              <div className="text-xs text-muted">
                {fileContent.data?.size_bytes ?? 0} bytes
              </div>
              <pre className="max-h-[60vh] overflow-auto rounded-md border bg-bg p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
                {fileContent.data?.content ?? ""}
              </pre>
            </div>
          )}
          <div className="mt-4 flex justify-end">
            <DialogClose asChild>
              <Button type="button" variant="secondary">Close</Button>
            </DialogClose>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function FireRecordCard({
  firedAt,
  firedBy,
  fireReason,
  firedByLabel,
}: {
  firedAt: string | null;
  firedBy: string | null;
  fireReason: string | null;
  firedByLabel: string | null;
}) {
  return (
    <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4 shadow-sm">
      <div className="text-[10px] uppercase tracking-wide text-red-700 dark:text-red-300">
        Termination record
      </div>
      <div className="mt-1 grid grid-cols-1 gap-2 text-sm md:grid-cols-3">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-muted">
            Fired at
          </div>
          <div className="font-mono">
            {firedAt ? new Date(firedAt).toLocaleString() : "—"}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-muted">
            Fired by
          </div>
          <div>
            {firedByLabel ?? (
              <span className="font-mono">{firedBy ?? "—"}</span>
            )}
          </div>
        </div>
        <div className="md:col-span-1">
          <div className="text-[10px] uppercase tracking-wide text-muted">
            Status
          </div>
          <div>
            <span className="inline-flex rounded-full bg-red-500/15 px-2 py-0.5 text-xs font-medium text-red-700 dark:text-red-300">
              fired
            </span>
          </div>
        </div>
      </div>
      <div className="mt-3">
        <div className="text-[10px] uppercase tracking-wide text-muted">
          Reason
        </div>
        <div className="mt-1 whitespace-pre-wrap rounded-md border border-red-500/30 bg-bg p-2 text-sm">
          {fireReason && fireReason.trim().length > 0
            ? fireReason
            : "(no reason recorded)"}
        </div>
      </div>
    </div>
  );
}

function WorkspaceCard({
  title,
  description,
  listing,
  error,
  onOpen,
}: {
  title: string;
  description: string;
  listing: WorkspaceListing | undefined;
  error: unknown;
  onOpen: (path: string) => void;
}) {
  return (
    <Card>
      <div className="mb-2">
        <div className="text-sm font-medium">{title}</div>
        <div className="text-xs text-muted">{description}</div>
      </div>
      {error ? (
        <div className="text-xs text-muted">
          No view permission or error: {(error as Error).message}
        </div>
      ) : null}
      {listing && listing.files.length === 0 ? (
        <div className="rounded-md border p-3 text-sm text-muted">No files.</div>
      ) : null}
      {listing && listing.files.length > 0 ? (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th className="py-1">Dosya</th>
              <th className="py-1">Boyut</th>
              <th className="py-1">Modified</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs">
            {listing.files.map((f) => (
              <tr key={f.relative_path} className="border-t">
                <td className="py-1">
                  {f.is_dir ? (
                    <span className="text-muted">{f.relative_path}/</span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => onOpen(f.relative_path)}
                      className="text-left text-accent underline-offset-2 hover:underline"
                    >
                      {f.relative_path}
                    </button>
                  )}
                </td>
                <td className="py-1">{f.size_bytes}</td>
                <td className="py-1">
                  {new Date(f.modified_at).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Card>
  );
}

function MetricGrid({ metrics }: { metrics: PerformanceReport["metrics"] | undefined }) {
  const items = [
    ["Tasks completed", metrics?.tasks_completed.toString() ?? "—"],
    ["Messages sent", metrics?.messages_sent.toString() ?? "—"],
    ["Messages received", metrics?.messages_received.toString() ?? "—"],
    [
      "Tool success",
      metrics?.tool_success_rate == null
        ? "—"
        : `${Math.round(metrics.tool_success_rate * 100)}%`,
    ],
    [
      "Median response",
      metrics?.avg_response_seconds == null
        ? "—"
        : `${metrics.avg_response_seconds.toFixed(1)}s`,
    ],
    ["Cut-off turns", metrics?.truncated_turns.toString() ?? "—"],
    ["Health alerts", metrics?.health_alerts.toString() ?? "—"],
    ["Tenure", metrics ? `${metrics.tenure_days}d` : "—"],
  ];
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {items.map(([label, value]) => (
        <div key={label} className="rounded-md border p-3">
          <div className="text-[10px] uppercase tracking-wide text-muted">
            {label}
          </div>
          <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
        </div>
      ))}
    </div>
  );
}

function FeedbackDialog({
  companyId,
  agentId,
  managerId,
  onDone,
}: {
  companyId: string;
  agentId: string;
  managerId: string;
  onDone: () => void;
}) {
  const toast = useToast();
  const [rating, setRating] = useState("4");
  const [note, setNote] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      api(`/companies/${companyId}/agents/${agentId}/feedback`, {
        method: "POST",
        json: { from_agent_id: managerId, rating: Number(rating), note },
      }),
    onSuccess: () => {
      setNote("");
      onDone();
      toast.success("Feedback saved");
    },
    onError: (err) => toast.error("Feedback failed", (err as Error).message),
  });
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">Give feedback</Button>
      </DialogTrigger>
      <DialogContent title="Give feedback">
        <div className="space-y-3">
          <div>
            <Label>Acting manager</Label>
            <Input value={managerId} readOnly />
          </div>
          <div>
            <Label>Rating</Label>
            <select
              className="h-10 w-full rounded-md border bg-card px-3 text-sm"
              value={rating}
              onChange={(event) => setRating(event.target.value)}
            >
              {[5, 4, 3, 2, 1].map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </div>
          <div>
            <Label>Note</Label>
            <Textarea value={note} onChange={(event) => setNote(event.target.value)} />
          </div>
          <div className="flex justify-end gap-2">
            <DialogClose asChild>
              <Button type="button" variant="ghost">Cancel</Button>
            </DialogClose>
            <Button
              type="button"
              onClick={() => mutation.mutate()}
              disabled={!note.trim() || mutation.isPending}
            >
              Save
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function RoleChangeDialog({
  companyId,
  agentId,
  managerId,
  currentTitle,
  currentDescription,
  onDone,
}: {
  companyId: string;
  agentId: string;
  managerId: string;
  currentTitle: string;
  currentDescription: string;
  onDone?: () => void;
}) {
  const toast = useToast();
  const [title, setTitle] = useState(currentTitle);
  const [description, setDescription] = useState(currentDescription);
  const [rationale, setRationale] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      api<{ request_id?: string | null; status: string }>(
        `/companies/${companyId}/agents/${agentId}/role-change`,
        {
          method: "POST",
          json: {
            from_agent_id: managerId,
            new_role_title: title,
            new_role_description: description,
            rationale,
          },
        },
      ),
    onSuccess: (data) => {
      toast.success("Role changed", data.status);
      onDone?.();
    },
    onError: (err) => toast.error("Role change failed", (err as Error).message),
  });
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="secondary">Role change</Button>
      </DialogTrigger>
      <DialogContent title="Change role">
        <div className="space-y-3">
          <div>
            <Label>Acting manager</Label>
            <Input value={managerId} readOnly />
          </div>
          <div>
            <Label>New title</Label>
            <Input value={title} onChange={(event) => setTitle(event.target.value)} />
          </div>
          <div>
            <Label>New description</Label>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <div>
            <Label>Rationale</Label>
            <Textarea
              value={rationale}
              onChange={(event) => setRationale(event.target.value)}
            />
          </div>
          <div className="flex justify-end gap-2">
            <DialogClose asChild>
              <Button type="button" variant="ghost">Cancel</Button>
            </DialogClose>
            <Button
              type="button"
              onClick={() => mutation.mutate()}
              disabled={!title.trim() || !description.trim() || !rationale.trim() || mutation.isPending}
            >
              Apply
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function FireDialog({
  companyId,
  agentId,
  managerId,
  onDone,
}: {
  companyId: string;
  agentId: string;
  managerId: string;
  onDone?: () => void;
}) {
  const toast = useToast();
  const [reason, setReason] = useState("");
  const mutation = useMutation({
    mutationFn: () =>
      api<{ result: string; request_id: string | null; reason: string | null }>(
        `/companies/${companyId}/agents/${agentId}/fire`,
        {
          method: "POST",
          json: { from_agent_id: managerId, reason },
        },
      ),
    onSuccess: (data) => {
      onDone?.();
      if (data.result === "denied") {
        toast.error("Fire action denied", data.reason || data.result);
        return;
      }
      toast.success("Agent fired", data.request_id || data.result);
    },
    onError: (err) => toast.error("Fire action failed", (err as Error).message),
  });
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="danger">Fire</Button>
      </DialogTrigger>
      <DialogContent title="Fire agent">
        <div className="space-y-3">
          <div>
            <Label>Acting manager</Label>
            <Input value={managerId} readOnly />
          </div>
          <div>
            <Label>Reason</Label>
            <Textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <div className="flex justify-end gap-2">
            <DialogClose asChild>
              <Button type="button" variant="ghost">Cancel</Button>
            </DialogClose>
            <Button
              type="button"
              variant="danger"
              onClick={() => mutation.mutate()}
              disabled={!reason.trim() || mutation.isPending}
            >
              Submit
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
