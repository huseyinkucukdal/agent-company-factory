"use client";

import { AuthGate } from "@/components/auth-gate";
import { Shell } from "@/components/shell";

export default function DashLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <Shell>{children}</Shell>
    </AuthGate>
  );
}
