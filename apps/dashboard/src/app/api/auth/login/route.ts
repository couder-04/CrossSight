import { NextResponse } from "next/server";
import { api } from "@/lib/api";
import { AUTH_COOKIE, USER_COOKIE, sessionCookieOptions } from "@/lib/auth";

const API_BASE =
  process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function POST(request: Request) {
  const body = await request.json();
  const { username, password, mode } = body as {
    username?: string;
    password?: string;
    mode?: string;
  };
  const source = mode === "video" ? "video" : "sim";

  if (!username || !password) {
    return NextResponse.json({ error: "Username and password required" }, { status: 400 });
  }

  try {
    const result = await api.login(username, password);
    const modeRes = await fetch(`${API_BASE}/sources/mode`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${result.access_token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ mode: source }),
      cache: "no-store",
    });
    if (!modeRes.ok) {
      const modeBody = (await modeRes.json().catch(() => ({}))) as { detail?: string; error?: string };
      const detail = modeBody.detail ?? modeBody.error ?? "Could not start that mode";
      return NextResponse.json({ error: detail }, { status: modeRes.status });
    }

    const response = NextResponse.json({
      username: result.username,
      role: result.role,
      mode: source,
      last_login_at: result.last_login_at ?? null,
      last_login_ip: result.last_login_ip ?? null,
    });

    const options = sessionCookieOptions(60 * 60 * 8);
    response.cookies.set(AUTH_COOKIE, result.access_token, options);
    response.cookies.set(
      USER_COOKIE,
      JSON.stringify({ username: result.username, role: result.role }),
      options,
    );
    response.cookies.set("anpr_source", source, { ...options, httpOnly: false });

    return response;
  } catch (err) {
    const status = err && typeof err === "object" && "status" in err ? (err as { status: number }).status : 500;
    const message = err instanceof Error ? err.message : "Login failed";
    return NextResponse.json({ error: message }, { status });
  }
}
