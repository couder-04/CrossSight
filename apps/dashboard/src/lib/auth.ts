import type { Role, UserSession } from "@/types";

export const AUTH_COOKIE = "anpr_token";
export const USER_COOKIE = "anpr_user";

export function canAccessTrack(role: Role): boolean {
  return role === "admin" || role === "operator";
}

export function canAccessAdmin(role: Role): boolean {
  return role === "admin";
}

export function navItemsForRole(role: Role) {
  const items = [
    { href: "/live", label: "Live", roles: ["admin", "operator", "analyst"] as Role[] },
    { href: "/track", label: "Track", roles: ["admin", "operator"] as Role[] },
    { href: "/analytics", label: "Analytics", roles: ["admin", "operator", "analyst"] as Role[] },
    { href: "/alerts", label: "Alerts", roles: ["admin", "operator", "analyst"] as Role[] },
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
