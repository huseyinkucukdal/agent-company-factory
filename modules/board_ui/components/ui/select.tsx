"use client";

import { cn } from "@/lib/cn";
import * as RS from "@radix-ui/react-select";
import { type ReactNode } from "react";

export const Select = RS.Root;
export const SelectGroup = RS.Group;
export const SelectValue = RS.Value;

export function SelectTrigger({
  children,
  className,
  placeholder,
}: {
  children?: ReactNode;
  className?: string;
  placeholder?: string;
}) {
  return (
    <RS.Trigger
      className={cn(
        "inline-flex h-9 min-w-[160px] items-center justify-between gap-2 rounded-md border bg-card px-3 text-sm outline-none focus:ring-2 focus:ring-accent",
        className,
      )}
    >
      {children ?? <RS.Value placeholder={placeholder} />}
      <RS.Icon className="opacity-60">▾</RS.Icon>
    </RS.Trigger>
  );
}

export function SelectContent({ children }: { children: ReactNode }) {
  return (
    <RS.Portal>
      <RS.Content
        position="popper"
        sideOffset={4}
        className="z-50 max-h-72 overflow-hidden rounded-md border bg-card text-sm shadow-lg"
      >
        <RS.Viewport className="p-1">{children}</RS.Viewport>
      </RS.Content>
    </RS.Portal>
  );
}

export function SelectItem({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <RS.Item
      value={value}
      className={cn(
        "flex cursor-pointer select-none items-center rounded px-2 py-1.5 outline-none data-[highlighted]:bg-bg data-[state=checked]:font-medium",
        className,
      )}
    >
      <RS.ItemText>{children}</RS.ItemText>
    </RS.Item>
  );
}
