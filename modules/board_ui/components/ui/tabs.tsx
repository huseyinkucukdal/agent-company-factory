"use client";

import { cn } from "@/lib/cn";
import * as RT from "@radix-ui/react-tabs";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { forwardRef } from "react";

export const Tabs = RT.Root;

export const TabsList = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof RT.List>
>(({ className, ...rest }, ref) => (
  <RT.List
    ref={ref}
    className={cn(
      "inline-flex items-center gap-1 rounded-md border bg-card p-1 text-sm",
      className,
    )}
    {...rest}
  />
));
TabsList.displayName = "TabsList";

export const TabsTrigger = forwardRef<
  HTMLButtonElement,
  ComponentPropsWithoutRef<typeof RT.Trigger>
>(({ className, ...rest }, ref) => (
  <RT.Trigger
    ref={ref}
    className={cn(
      "rounded-md px-3 py-1.5 text-muted transition data-[state=active]:bg-bg data-[state=active]:text-fg data-[state=active]:shadow-sm",
      className,
    )}
    {...rest}
  />
));
TabsTrigger.displayName = "TabsTrigger";

export const TabsContent = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof RT.Content> & { children: ReactNode }
>(({ className, ...rest }, ref) => (
  <RT.Content
    ref={ref}
    className={cn("mt-4 outline-none", className)}
    {...rest}
  />
));
TabsContent.displayName = "TabsContent";
