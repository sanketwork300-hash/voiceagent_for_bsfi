"use client";
import { DEMO_MODE } from "@/config/env";
import { CommandPalette } from "./command-palette";
import { BottomNav, MobileDrawer } from "./mobile-nav";
import { SessionTimeout } from "./session-timeout";
import { Sidebar } from "./sidebar";
import { TopBar } from "./topbar";

export function DemoBanner() {
  if (!DEMO_MODE) return null;
  return (
    <div role="note" className="border-b border-warning/25 bg-warning/[0.06] px-4 py-1 text-center text-meta text-warning">
      Demo environment — sample bank and fictional customers. One-time passwords are always 123456.
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-dvh overflow-hidden">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[var(--z-palette)] focus:rounded-control focus:bg-strong focus:px-3 focus:py-2 focus:text-background">Skip to content</a>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <DemoBanner />
        <TopBar />
        <main id="main" tabIndex={-1} className="min-h-0 flex-1 overflow-y-auto pb-20 focus:outline-none lg:pb-0">{children}</main>
      </div>
      <MobileDrawer />
      <BottomNav />
      <CommandPalette />
      <SessionTimeout />
    </div>
  );
}

export function PageContainer({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return <div className={`mx-auto w-full px-4 py-6 sm:px-6 lg:px-8 ${wide ? "max-w-[1600px]" : "max-w-[1280px]"}`}>{children}</div>;
}
