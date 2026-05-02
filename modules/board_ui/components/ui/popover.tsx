"use client";

import { cn } from "@/lib/cn";
import * as RP from "@radix-ui/react-popover";
import { type ReactNode } from "react";

export const Popover = RP.Root;
export const PopoverTrigger = RP.Trigger;
export const PopoverAnchor = RP.Anchor;

export function PopoverContent({
  children,
  className,
  align = "start",
  sideOffset = 6,
}: {
  children: ReactNode;
  className?: string;
  align?: "start" | "center" | "end";
  sideOffset?: number;
}) {
  return (
    <RP.Portal>
      <RP.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 min-w-[200px] rounded-md border bg-card p-2 text-sm shadow-lg outline-none",
          className,
        )}
      >
        {children}
      </RP.Content>
    </RP.Portal>
  );
}
