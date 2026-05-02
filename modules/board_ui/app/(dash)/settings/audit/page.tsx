"use client";

import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";

interface AuditEntry {
  id: number;
  user_id: string;
  action: string;
  target?: string | null;
  ts: string;
  ip?: string | null;
  payload?: Record<string, unknown>;
}

export default function AuditPage() {
  const q = useQuery({
    queryKey: ["audit"],
    queryFn: () => api<AuditEntry[]>("/audit?limit=200"),
  });
  return (
    <div className="space-y-3 p-6">
      <h1 className="text-lg font-semibold">Board audit log</h1>
      <Card>
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th className="py-1">#</th>
              <th className="py-1">user</th>
              <th className="py-1">action</th>
              <th className="py-1">target</th>
              <th className="py-1">ts</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs">
            {q.data?.map((e) => (
              <tr key={e.id} className="border-t">
                <td className="py-1">{e.id}</td>
                <td className="py-1">{e.user_id}</td>
                <td className="py-1">{e.action}</td>
                <td className="py-1">{e.target ?? ""}</td>
                <td className="py-1">{e.ts}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
