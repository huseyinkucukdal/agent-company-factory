"use client";

import { useAuth } from "@/lib/auth-store";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

/**
 * Block child rendering until the persist middleware finishes loading
 * the session out of localStorage. If we redirect on the first synchronous
 * render, the user is bounced to /login on every page refresh because
 * zustand's hydration hasn't run yet.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const hydrated = useAuth((s) => s.hydrated);
  const token = useAuth((s) => s.token);

  useEffect(() => {
    if (!hydrated) return;
    if (!token) router.replace("/login");
  }, [hydrated, token, router]);

  if (!hydrated) {
    return (
      <div className="grid h-screen place-items-center text-sm text-muted-foreground">
        Loading…
      </div>
    );
  }
  if (!token) return null;
  return <>{children}</>;
}
