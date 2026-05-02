"use client";

import { cn } from "@/lib/cn";
import * as RD from "@radix-ui/react-dialog";
import { type ReactNode } from "react";

export const Dialog = RD.Root;
export const DialogTrigger = RD.Trigger;
export const DialogClose = RD.Close;
export const DialogPortal = RD.Portal;

export function DialogContent({
  children,
  className,
  title,
  description,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
  description?: string;
}) {
  return (
    <RD.Portal>
      <RD.Overlay className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm" />
      <RD.Content
        className={cn(
          "fixed left-1/2 top-1/2 z-50 w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-card p-5 shadow-xl outline-none",
          className,
        )}
      >
        {title ? (
          <RD.Title className="mb-1 text-base font-semibold">{title}</RD.Title>
        ) : (
          <RD.Title className="sr-only">Dialog</RD.Title>
        )}
        {description ? (
          <RD.Description className="mb-3 text-sm text-muted">
            {description}
          </RD.Description>
        ) : (
          <RD.Description className="sr-only">…</RD.Description>
        )}
        {children}
      </RD.Content>
    </RD.Portal>
  );
}
