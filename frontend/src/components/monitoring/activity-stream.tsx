"use client";
import { useQuery } from "@tanstack/react-query";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusDot, healthTone } from "@/components/ui/status-dot";
import { useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import type { ActivityItem } from "@/types/domain";
import { formatMs, formatRelative, titleCase } from "@/utils/format";

function describe(a: ActivityItem): { title: string; detail: string } {
  if (a.kind === "handoff") return { title: "Human handoff requested", detail: a.detail ? titleCase(a.detail) : "" };
  if (a.kind === "auth") return { title: titleCase(a.title.replace("auth.", "")), detail: `${a.channel ?? ""} · ${a.status}` };
  const held = a.status === "pending";
  return {
    title: a.title,
    detail: a.status === "completed" ? `completed in ${formatMs(a.latency_ms)} · ${a.channel}`
      : held ? `waiting on policy (${titleCase(a.detail ?? "")}) · ${a.channel}` : `${a.status}${a.detail ? ` · ${titleCase(a.detail)}` : ""} · ${a.channel}`,
  };
}

export function ActivityStream({ limit = 15 }: { limit?: number }) {
  const t = useTenantKey();
  const q = useQuery({ queryKey: [t, "activity", limit], queryFn: () => apiClient.monitoring.activity(limit), refetchInterval: 5000 });
  if (q.error) return <ErrorState compact error={q.error} title="Live activity unavailable" onRetry={() => q.refetch()} />;
  if (!q.data) return <SkeletonRows rows={6} label="Loading activity" />;
  if (!q.data.length) return <p className="py-6 text-small text-muted">No activity yet. Tool calls, verifications and handoffs appear here as customers talk to the agent.</p>;
  return (
    <ol className="divide-y divide-border" aria-label="Live activity" aria-live="polite">
      {q.data.map((a) => {
        const d = describe(a);
        return (
          <li key={`${a.kind}-${a.id}`} className="grid grid-cols-[14px_1fr_auto] items-start gap-x-3 py-2.5">
            <StatusDot tone={a.kind === "handoff" ? "info" : healthTone(a.status)} className="mt-1.5" />
            <div className="min-w-0">
              <p className={a.kind === "tool" ? "truncate font-mono text-[12.5px] text-foreground" : "truncate text-small text-foreground"}>{d.title}</p>
              <p className="truncate text-meta text-muted">{d.detail}</p>
            </div>
            <time className="text-meta text-subtle" dateTime={a.at}>{formatRelative(a.at)}</time>
          </li>
        );
      })}
    </ol>
  );
}
