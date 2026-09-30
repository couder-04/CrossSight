import type { Role, UserSession } from "@/types";

export const AUTH_COOKIE = "anpr_token";
export const USER_COOKIE = "anpr_user";

/** Session cookies: HttpOnly always, Secure only when the dashboard is served over HTTPS. */
export function sessionCookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge,
  };
}

export function canAccessTrack(role: Role): boolean {
  return role === "admin" || role === "operator";
}

export function canAccessAdmin(role: Role): boolean {
  return role === "admin";
}

export function navItemsForRole(role: Role) {
  const items = [
    { href: "/live", label: "Live", roles: ["admin", "operator", "analyst"] as Role[] },
    { href: "/wall", label: "Wall", roles: ["admin", "operator"] as Role[] },
    { href: "/track", label: "Track", roles: ["admin", "operator"] as Role[] },
    { href: "/flow", label: "Flow", roles: ["admin", "operator", "analyst"] as Role[] },
    { href: "/analytics", label: "Analytics", roles: ["admin", "operator", "analyst"] as Role[] },
    { href: "/alerts", label: "Alerts", roles: ["admin", "operator", "analyst"] as Role[] },
    { href: "/health", label: "Health", roles: ["admin", "operator", "analyst"] as Role[] },
    { href: "/review", label: "Review", roles: ["admin", "operator"] as Role[] },
    { href: "/investigate", label: "Investigate", roles: ["admin", "operator"] as Role[] },
    { href: "/imports", label: "Import", roles: ["admin", "operator"] as Role[] },
    { href: "/exports", label: "Export", roles: ["admin", "operator", "analyst"] as Role[] },
    { href: "/admin", label: "Admin", roles: ["admin"] as Role[] },
  ];
  return items.filter((item) => item.roles.includes(role));
}

export function parseUserCookie(raw: string | undefined): UserSession | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as UserSession;
    if (!parsed.username || !parsed.role) return null;
    return parsed;
  } catch {
    return null;
  }
}
