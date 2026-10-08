export function AuthFrame({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center px-4 py-12">
      <div className="grid w-full max-w-sm gap-8">
        <div className="flex items-center gap-2 text-small font-semibold text-strong">
          <svg aria-hidden viewBox="0 0 20 20" className="size-5"><rect x="1" y="1" width="18" height="18" rx="4" fill="none" stroke="currentColor" strokeWidth="1.25" /><path d="M5 13V7m3.3 6V5m3.4 8V8.5M15 13v-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
          Ledgerline
        </div>
        {children}
      </div>
    </main>
  );
}
