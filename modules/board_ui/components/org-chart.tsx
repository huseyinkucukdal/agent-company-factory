"use client";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
} from "@/components/ui/dialog";
import { StatusPill } from "@/components/ui/card";
import type { Agent } from "@/lib/types";
import Link from "next/link";
import { useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  type Edge,
  type Node,
  type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";

interface OrgChartProps {
  companyId: string;
  agents: Agent[];
}

const NODE_W = 200;
const NODE_H = 80;
const H_GAP = 32;
const V_GAP = 56;

interface NodeData {
  agent: Agent;
  onSelect: (a: Agent) => void;
}

function AgentNode({ data }: NodeProps<NodeData>) {
  const { agent, onSelect } = data;
  return (
    <div
      onClick={() => onSelect(agent)}
      className="flex h-full w-full cursor-pointer flex-col gap-1 rounded-md border bg-card p-2 text-left shadow-sm transition hover:border-accent"
    >
      <Handle
        type="target"
        position={Position.Top}
        className="!h-1 !w-1 !border-0 !bg-border"
      />
      <div className="flex items-center justify-between gap-2">
        <div className="truncate text-sm font-medium">{agent.role}</div>
        <StatusPill status={agent.status} />
      </div>
      <div className="truncate font-mono text-[10px] text-muted">{agent.id}</div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="!h-1 !w-1 !border-0 !bg-border"
      />
    </div>
  );
}

const nodeTypes = { agent: AgentNode };

interface LayoutEntry {
  agent: Agent;
  x: number;
  y: number;
}

/** Recursive top-down layout. Returns absolute (x, y) for each agent. */
function layout(agents: Agent[]): { entries: LayoutEntry[]; width: number } {
  const byParent = new Map<string | null, Agent[]>();
  for (const a of agents) {
    const key = a.reports_to ?? null;
    const list = byParent.get(key) ?? [];
    list.push(a);
    byParent.set(key, list);
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => a.role.localeCompare(b.role));
  }
  const ids = new Set(agents.map((a) => a.id));
  // Roots = no reports_to OR reports_to points outside the set.
  const roots = agents.filter(
    (a) => !a.reports_to || !ids.has(a.reports_to),
  );

  const entries: LayoutEntry[] = [];
  const unit = NODE_W + H_GAP;
  let cursor = 0;

  function place(a: Agent, depth: number) {
    const start = cursor;
    const kids = byParent.get(a.id) ?? [];
    if (kids.length === 0) cursor += 1;
    else for (const k of kids) place(k, depth + 1);
    const myCenterCol =
      kids.length === 0 ? start : (start + cursor - 1) / 2;
    const x = Math.round(myCenterCol * unit);
    const y = depth * (NODE_H + V_GAP);
    entries.push({ agent: a, x, y });
  }
  for (const r of roots) place(r, 0);

  const totalCols = Math.max(cursor, 1);
  const width = totalCols * unit;
  return { entries, width };
}

export function OrgChart({ companyId, agents }: OrgChartProps) {
  const [selected, setSelected] = useState<Agent | null>(null);

  const { nodes, edges } = useMemo<{ nodes: Node<NodeData>[]; edges: Edge[] }>(() => {
    const { entries } = layout(agents);
    const ns: Node<NodeData>[] = entries.map(({ agent, x, y }) => ({
      id: agent.id,
      type: "agent",
      position: { x, y },
      data: { agent, onSelect: setSelected },
      width: NODE_W,
      height: NODE_H,
      style: { width: NODE_W, height: NODE_H },
      draggable: false,
      selectable: false,
    }));
    const ids = new Set(agents.map((a) => a.id));
    const es: Edge[] = [];
    for (const a of agents) {
      if (a.reports_to && ids.has(a.reports_to)) {
        es.push({
          id: `${a.reports_to}->${a.id}`,
          source: a.reports_to,
          target: a.id,
          type: "smoothstep",
          style: { strokeWidth: 1.5 },
        });
      }
    }
    return { nodes: ns, edges: es };
  }, [agents]);

  if (!agents.length) {
    return (
      <div className="flex h-[420px] items-center justify-center text-xs text-muted">
        No agents yet.
      </div>
    );
  }

  return (
    <>
      <div className="h-[480px] rounded-md border bg-bg">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          panOnScroll
          minZoom={0.4}
          maxZoom={1.4}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
        >
          <Background color="var(--border)" gap={24} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <Dialog
        open={selected !== null}
        onOpenChange={(o) => !o && setSelected(null)}
      >
        <DialogContent
          title={selected?.role ?? "Agent"}
          description={selected ? `Status: ${selected.status}` : undefined}
        >
          {selected ? (
            <div className="space-y-3 text-sm">
              <div className="space-y-1">
                <Row label="ID" value={selected.id} mono />
                <Row label="Persona" value={selected.persona_ref} mono />
                <Row
                  label="Manager"
                  value={selected.reports_to ?? "—"}
                  mono
                />
                <Row
                  label="Hired"
                  value={new Date(selected.hired_at).toLocaleString()}
                />
              </div>
              <div className="flex justify-end gap-2">
                <DialogClose asChild>
                  <Button variant="secondary" size="sm">
                    Close
                  </Button>
                </DialogClose>
                <Link
                  href={`/companies/${companyId}/agents/${selected.id}`}
                  className="inline-flex h-8 items-center rounded-md bg-accent px-3 text-sm font-medium text-white hover:opacity-90"
                >
                  Details
                </Link>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="text-muted">{label}</span>
      <span className={mono ? "font-mono text-[11px]" : "text-fg"}>
        {value}
      </span>
    </div>
  );
}
