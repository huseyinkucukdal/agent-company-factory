"use client";

import { useAuth } from "@/lib/auth-store";
import { api } from "@/lib/api";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/cn";

const NAV: { href: string; label: string; admin?: boolean }[] = [
  { href: "/", label: "Companies" },
  { href: "/approvals", label: "Approvals" },
  { href: "/links", label: "Links" },
  { href: "/settings/users", label: "Users", admin: true },
  { href: "/settings/audit", label: "Audit", admin: true },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";
  const router = useRouter();
  const { user, token, clear } = useAuth();

  async function logout() {
    if (token?.refresh_token) {
      try {
        await api("/auth/logout", { method: "POST", json: { refresh_token: token.refresh_token } });
      } catch {
        /* ignore */
      }
    }
    clear();
    router.replace("/login");
  }

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 flex-col border-r bg-card">
        <div className="border-b px-4 py-4">
          <div className="text-sm font-semibold">ACF Board</div>
          <div className="text-xs text-muted">{user?.email}</div>
          <div className="mt-1 text-[10px] uppercase tracking-wide text-muted">
            {user?.role}
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-1 p-2">
          {NAV.filter((n) => !n.admin || user?.role === "admin").map((n) => {
            const active =
              n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={cn(
                  "rounded-md px-3 py-2 text-sm transition hover:bg-bg",
                  active && "bg-bg font-medium",
                )}
              >
                {n.label}
              </Link>
            );
          })}
        </nav>
        <button
          onClick={logout}
          className="border-t px-4 py-3 text-left text-sm text-muted hover:text-fg"
        >
          Log out
        </button>
      </aside>
      <main className="flex-1 overflow-x-hidden">{children}</main>
    </div>
  );
}
