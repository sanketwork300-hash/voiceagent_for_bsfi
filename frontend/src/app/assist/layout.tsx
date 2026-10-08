import type { Metadata } from "next";
import Link from "next/link";
import { DemoBanner } from "@/components/layout/app-shell";

export const metadata: Metadata = { title: { default: "Demo Bank assistant", template: "%s · Demo Bank" } };

/** Customer-facing portal: no operator navigation, no internals. */
export default function AssistLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-dvh flex-col">
      <DemoBanner />
      <header className="flex h-12 shrink-0 items-center justify-between border-b border-border px-4">
        <Link href="/assist" className="text-small font-semibold text-strong">Demo Bank</Link>
        <nav aria-label="Assistant" className="flex gap-1 text-small">
          <Link href="/assist" className="rounded-control px-2.5 py-1 text-muted hover:bg-hover hover:text-foreground">Chat</Link>
          <Link href="/assist/voice" className="rounded-control px-2.5 py-1 text-muted hover:bg-hover hover:text-foreground">Call</Link>
          <Link href="/" className="rounded-control px-2.5 py-1 text-muted hover:bg-hover hover:text-foreground">Exit</Link>
        </nav>
      </header>
      <main id="main" className="min-h-0 flex-1">{children}</main>
    </div>
  );
}
