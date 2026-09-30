"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [bgOk, setBgOk] = useState(true);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.error ?? "Login failed");
      }
      if (body.last_login_at) {
        sessionStorage.setItem(
          "crosssight:last-login",
          JSON.stringify({ last_login_at: body.last_login_at, last_login_ip: body.last_login_ip ?? null }),
        );
      }
      const from = params.get("from") ?? "/live";
      router.push(from);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative min-h-screen flex items-center justify-center p-4">
      <img src="/login-bg.jpg" alt="" className="hidden" onError={() => setBgOk(false)} />
      {bgOk && <div className="absolute inset-0 bg-cover bg-center login-bg" />}
      <div className={`absolute inset-0 ${bgOk ? "bg-surface/85 backdrop-blur-sm" : "bg-surface"}`} />
      <div className="relative w-full max-w-sm rounded-lg border border-border bg-surface-raised p-6 shadow-xl">
        <div className="mb-6 text-center">
          <p className="font-mono text-2xl text-accent tracking-tight">ANPR</p>
          <p className="text-sm text-muted mt-1">Control room sign-in</p>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <Input
            label="Username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            required
          />
          <Input
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
          {error && <p className="text-danger text-sm">{error}</p>}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "Signing in…" : "Sign in"}
          </Button>
        </form>

        {process.env.NEXT_PUBLIC_APP_ENV === "dev" && (
          <details className="mt-4 text-[10px] text-muted text-center">
            <summary className="cursor-pointer">Demo credentials</summary>
            admin / admin123 · operator / operator123 · analyst / analyst123
          </details>
        )}
      </div>
    </div>
  );
}
