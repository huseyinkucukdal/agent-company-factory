"use client";

import { cn } from "@/lib/cn";
import * as RT from "@radix-ui/react-toast";
import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

type ToastTone = "default" | "success" | "error";

interface ToastItem {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
}

interface ToastApi {
  push: (t: Omit<ToastItem, "id" | "tone"> & { tone?: ToastTone }) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
}

const ToastCtx = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("useToast must be used within <ToastProvider>");
  return ctx;
}

const TONE_STYLES: Record<ToastTone, string> = {
  default: "border-border bg-card text-fg",
  success: "border-emerald-600/40 bg-emerald-600/10 text-emerald-700 dark:text-emerald-200",
  error: "border-red-600/40 bg-red-600/10 text-red-700 dark:text-red-200",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback<ToastApi["push"]>((t) => {
    const id = Date.now() + Math.random();
    setItems((prev) => [
      ...prev,
      { id, title: t.title, description: t.description, tone: t.tone ?? "default" },
    ]);
  }, []);
  const success = useCallback<ToastApi["success"]>(
    (title, description) => push({ title, description, tone: "success" }),
    [push],
  );
  const error = useCallback<ToastApi["error"]>(
    (title, description) => push({ title, description, tone: "error" }),
    [push],
  );

  return (
    <ToastCtx.Provider value={{ push, success, error }}>
      <RT.Provider swipeDirection="right" duration={4000}>
        {children}
        {items.map((it) => (
          <RT.Root
            key={it.id}
            onOpenChange={(open) => {
              if (!open) {
                setItems((prev) => prev.filter((x) => x.id !== it.id));
              }
            }}
            className={cn(
              "rounded-md border p-3 text-sm shadow-lg data-[state=open]:animate-in data-[state=closed]:animate-out",
              TONE_STYLES[it.tone],
            )}
          >
            <RT.Title className="font-medium">{it.title}</RT.Title>
            {it.description ? (
              <RT.Description className="mt-1 text-xs opacity-80">
                {it.description}
              </RT.Description>
            ) : null}
          </RT.Root>
        ))}
        <RT.Viewport className="fixed bottom-4 right-4 z-[100] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2 outline-none" />
      </RT.Provider>
    </ToastCtx.Provider>
  );
}
