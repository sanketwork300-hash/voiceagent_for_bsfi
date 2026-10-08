import { cn } from "@/utils/cn";

export function PageHeader({ title, description, actions, meta }: { title: string; description?: string; actions?: React.ReactNode; meta?: React.ReactNode }) {
  return (
    <header className="flex flex-col gap-3 pb-6 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-title font-semibold tracking-[-0.02em] text-strong">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-body text-muted">{description}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-2 text-meta text-muted">{meta}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </header>
  );
}

export function Section({ title, description, actions, children, className }: { title: string; description?: string; actions?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("grid content-start gap-3", className)} aria-label={title}>
      <div className="flex items-end justify-between gap-3">
        <div>
          <h2 className="text-section font-medium tracking-[-0.01em] text-strong">{title}</h2>
          {description && <p className="text-small text-muted">{description}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Panel({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-panel border border-border bg-surface", className)} {...p} />;
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded-[4px] border border-border-strong bg-raised px-1.5 font-mono text-[10.5px] text-muted">{children}</kbd>;
}

export function KeyValue({ items, className }: { items: { label: string; value: React.ReactNode; mono?: boolean }[]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(110px,auto)_1fr] gap-x-6 gap-y-2 text-small", className)}>
      {items.map((it) => (
        <div key={it.label} className="contents">
          <dt className="text-muted">{it.label}</dt>
          <dd className={cn("min-w-0 break-words text-foreground", it.mono && "font-mono text-[12.5px]")}>{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Marks data that comes from a mock/sample source rather than a live backend API. */
export function SampleDataNote({ what }: { what: string }) {
  return (
    <p className="inline-flex items-center gap-1.5 rounded-[4px] border border-warning/30 px-1.5 py-px text-meta text-warning">
      Sample data · {what} isn&apos;t served by the backend yet
    </p>
  );
}
