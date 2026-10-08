import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";

export default async function Start() {
  if ((await cookies()).get("bfsi_staff")?.value) redirect("/dashboard");
  return (
    <main className="grid min-h-dvh grid-rows-[1fr_auto]">
      <div className="mx-auto grid w-full max-w-5xl content-center gap-12 px-6 py-16 md:grid-cols-[1.2fr_1fr] md:gap-16">
        <div className="grid content-center gap-5">
          <p className="text-small text-muted">Demo Bank · AI service desk</p>
          <h1 className="text-[clamp(32px,5vw,52px)] font-semibold leading-[1.05] tracking-[-0.035em] text-strong">
            Talk to your bank.<br />In your language.
          </h1>
          <p className="max-w-md text-lead text-muted">
            Ask about balances, loans, cards and payments by chat or voice. Anything that moves money waits for your explicit confirmation.
          </p>
        </div>
        <div className="grid content-center gap-3">
          <Link href="/assist" className="group grid gap-1 rounded-panel border border-border-strong bg-surface p-5 hover:border-[#3a3a3a] hover:bg-hover">
            <span className="text-lead font-medium text-strong">I&apos;m a customer</span>
            <span className="text-small text-muted">Chat or call the assistant.</span>
          </Link>
          <Link href="/auth/login" className="group grid gap-1 rounded-panel border border-border bg-background p-5 hover:border-border-strong hover:bg-surface">
            <span className="text-lead font-medium text-foreground">I work at the bank</span>
            <span className="text-small text-muted">Sign in to the operator console.</span>
          </Link>
        </div>
      </div>
      <footer className="border-t border-border px-6 py-4 text-meta text-subtle">Demo environment. Sample institution and fictional customers.</footer>
    </main>
  );
}
