"use client";

import { cn } from "@/lib/cn";
import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [
  ["Overview", ""],
  ["Approvals", "approvals"],
  ["Team", "agents"],
  ["Workspace", "workspace"],
  ["Budget", "budget"],
  ["Settings", "settings"],
  ["Replay", "replay"],
  ["Links", "links"],
] as const;

export function CompanyNav({ companyId }: { companyId: string }) {
  const pathname = usePathname() ?? "";
  const base = `/companies/${companyId}`;

  return (
    <nav className="border-b px-6 pt-4">
      <div className="flex flex-wrap gap-2 pb-2 text-sm">
        {ITEMS.map(([label, segment]) => {
          const href = segment ? `${base}/${segment}` : base;
          const active = segment
            ? pathname === href || pathname.startsWith(`${href}/`)
            : pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "rounded-md px-3 py-1 text-muted hover:bg-card hover:text-fg",
                active && "bg-card font-medium text-fg",
              )}
            >
              {label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
