"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { navItemsForRole } from "@/lib/auth";
import { cn } from "@/lib/utils";
import type { Role, UserSession } from "@/types";

export function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<UserSession | null>(null);

  useEffect(() => {
    fetch("/api/auth/me")
      .then((r) => (r.ok ? r.json() : null))
      .then(setUser)
      .catch(() => setUser(null));
  }, []);

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  const items = user ? navItemsForRole(user.role as Role) : [];

  return (
    <header className="h-12 border-b border-border bg-surface-raised flex items-center px-4 gap-6 shrink-0">
      <Link href="/live" className="flex items-center gap-2 font-semibold tracking-tight">
        <span className="font-mono text-accent text-lg">ANPR</span>
        <span className="text-xs text-muted hidden sm:inline">Control Room</span>
      </Link>

      <nav className="flex items-center gap-1 flex-1">
        {items.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={cn(
              "px-3 py-1.5 rounded text-sm transition-colors",
              pathname.startsWith(item.href)
                ? "bg-surface-overlay text-white"
                : "text-muted hover:text-slate-100 hover:bg-surface-overlay/60",
            )}
          >
            {item.label}
          </Link>
        ))}
      </nav>

      <div className="flex items-center gap-3 text-xs">
        {user && (
          <span className="text-muted font-mono">
            {user.username}
            <span className="text-accent ml-2 uppercase">{user.role}</span>
          </span>
        )}
        <Button variant="ghost" size="sm" onClick={logout}>
          Logout
        </Button>
      </div>
    </header>
  );
}
