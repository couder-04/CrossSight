import { Nav } from "@/components/layout/Nav";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <Nav />
      <main className="flex-1 min-h-0 overflow-hidden">{children}</main>
    </div>
  );
}
