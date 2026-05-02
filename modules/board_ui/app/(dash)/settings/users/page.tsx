"use client";

import { Card, StatusPill } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import type { User } from "@/lib/types";

export default function UsersPage() {
  const q = useQuery({
    queryKey: ["users"],
    queryFn: () => api<User[]>("/users"),
  });
  return (
    <div className="space-y-3 p-6">
      <h1 className="text-lg font-semibold">Users</h1>
      {q.error && (
        <div className="text-sm text-red-600">{(q.error as Error).message}</div>
      )}
      {q.data?.map((u) => (
        <Card key={u.id}>
          <div className="flex items-center justify-between">
            <div>
              <div className="font-medium">{u.email}</div>
              <div className="font-mono text-[11px] text-muted">{u.id}</div>
            </div>
            <StatusPill status={u.role} />
          </div>
        </Card>
      ))}
    </div>
  );
}
