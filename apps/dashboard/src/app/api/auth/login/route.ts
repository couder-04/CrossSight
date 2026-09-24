import { NextResponse } from "next/server";
import { api } from "@/lib/api";
import { AUTH_COOKIE, USER_COOKIE } from "@/lib/auth";

export async function POST(request: Request) {
  const body = await request.json();
  const { username, password } = body as { username?: string; password?: string };

  if (!username || !password) {
    return NextResponse.json({ error: "Username and password required" }, { status: 400 });
  }

  try {
    const result = await api.login(username, password);
    const response = NextResponse.json({
      username: result.username,
      role: result.role,
    });

    response.cookies.set(AUTH_COOKIE, result.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "lax",
      path: "/",
      maxAge: 60 * 60 * 8,
    });

    response.cookies.set(
      USER_COOKIE,
      JSON.stringify({ username: result.username, role: result.role }),
      {
        httpOnly: false,
        secure: process.env.NODE_ENV === "production",
        sameSite: "lax",
        path: "/",
        maxAge: 60 * 60 * 8,
      },
    );

    return response;
  } catch (err) {
    const status = err && typeof err === "object" && "status" in err ? (err as { status: number }).status : 500;
    const message = err instanceof Error ? err.message : "Login failed";
    return NextResponse.json({ error: message }, { status });
  }
}
