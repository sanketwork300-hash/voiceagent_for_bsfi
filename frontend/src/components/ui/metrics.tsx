"use client";
import { Line, LineChart, ResponsiveContainer } from "recharts";
import { cn } from "@/utils/cn";

/** Compact metric: label, value, optional sparkline. Rows of these replace big KPI cards. */
export function Metric({ label, value, sub, series, tone, className }: {
  label: string; value: React.ReactNode; sub?: React.ReactNode; series?: number[]; tone?: "success" | "warning" | "danger"; className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 items-end justify-between gap-3 px-4 py-3", className)}>
      <div className="min-w-0">
        <p className="text-meta text-muted">{label}</p>
        <p className={cn("tabular mt-0.5 text-[22px] font-semibold leading-tight tracking-[-0.02em] text-strong",
          tone === "danger" && "text-danger", tone === "warning" && "text-warning")}>{value}</p>
        {sub && <p className="mt-0.5 text-meta text-muted">{sub}</p>}
      </div>
      {series && series.length > 1 && <Sparkline data={series} />}
    </div>
  );
}

export function Sparkline({ data, width = 88, height = 28 }: { data: number[]; width?: number; height?: number }) {
  return (
    <div style={{ width, height }} aria-hidden className="shrink-0">
      <ResponsiveContainer width="100%" height="100%">
        {/* decorative: the value is in text beside it, so keep it out of the tab order */}
        <LineChart accessibilityLayer={false} data={data.map((v, i) => ({ i, v }))} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
          <Line type="monotone" dataKey="v" stroke="#9a9a9a" strokeWidth={1.25} dot={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function MetricStrip({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn("grid divide-y divide-border overflow-hidden rounded-panel border border-border bg-surface sm:grid-cols-2 sm:divide-x lg:grid-cols-4 lg:divide-y-0", className)}>
      {children}
    </div>
  );
}
