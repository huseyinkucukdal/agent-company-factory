"use client";

import { cn } from "@/lib/cn";
import type { HTMLAttributes } from "react";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-lg border bg-card p-4 shadow-sm", className)}
      {...rest}
    />
  );
}

export function StatusPill({
  status,
}: {
  status: string;
}) {
  const tone =
    status === "active" || status === "running"
      ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
      : status === "paused"
        ? "bg-amber-500/15 text-amber-700 dark:text-amber-300"
        : status === "closed" || status === "fired"
          ? "bg-zinc-500/15 text-muted"
          : status === "pending"
            ? "bg-blue-500/15 text-blue-700 dark:text-blue-300"
            : "bg-zinc-500/15 text-muted";
  return (
    <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", tone)}>
      {status}
    </span>
  );
}
