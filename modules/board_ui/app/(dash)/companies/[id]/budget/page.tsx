"use client";

import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { BudgetState } from "@/lib/types";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";

export default function BudgetPage() {
  const params = useParams<{ id: string }>() ?? { id: "" };
  const cid = params.id;
  const q = useQuery({
    queryKey: ["company", cid, "budget"],
    queryFn: () => api<BudgetState>(`/companies/${cid}/budget`),
  });
  const b = q.data;

  const Stat = ({ label, value }: { label: string; value: string }) => (
    <Card>
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold">${value}</div>
    </Card>
  );

  return (
    <div className="space-y-4 p-6">
      <h1 className="text-lg font-semibold">Budget</h1>
      {q.isLoading && <div className="text-sm text-muted">Loading…</div>}
      {b && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Toplam" value={b.total_usd} />
            <Stat label="Harcanan" value={b.spent_usd} />
            <Stat label="Rezerve" value={b.reserved_usd} />
            <Stat label="Kalan" value={b.remaining_usd} />
          </div>
          <Card>
            <div className="mb-2 text-sm font-medium">Spending by category</div>
            <table className="w-full text-sm">
              <tbody>
                {Object.entries(b.by_category).map(([cat, amount]) => (
                  <tr key={cat} className="border-t">
                    <td className="py-1 font-mono text-xs">{cat}</td>
                    <td className="py-1 text-right font-mono">${amount}</td>
                  </tr>
                ))}
                {Object.keys(b.by_category).length === 0 && (
                  <tr>
                    <td className="py-2 text-xs text-muted">
                      No spending yet
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </Card>
          {b.blocked && (
            <div className="rounded-md bg-red-500/10 px-3 py-2 text-sm text-red-700">
              Budget blocked
            </div>
          )}
        </>
      )}
    </div>
  );
}
