"use client";
import { useQuery } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import Link from "next/link";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { StatusDot, type DotTone } from "@/components/ui/status-dot";
import { useProfile, useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";

interface Note { id: string; tone: DotTone; title: string; detail: string; href: string }

/** Derived from live data (approvals, handoffs, integration health, monitoring) — no intrusive popups. */
export function useNotifications(): Note[] {
  const { can } = useProfile();
  const t = useTenantKey();
  const opts = { refetchInterval: 30_000, retry: false } as const;
  const approvals = useQuery({ queryKey: [t, "approvals"], queryFn: apiClient.approvals.list, enabled: can("approval.execute"), ...opts });
  const handoffs = useQuery({ queryKey: [t, "handoff", "queued"], queryFn: () => apiClient.handoff.queue("queued"), enabled: can("handoff.handle"), ...opts });
  const integ = useQuery({ queryKey: [t, "integrations"], queryFn: apiClient.integrations.list, enabled: can("integration.manage"), ...opts });
  const mon = useQuery({ queryKey: [t, "monitoring", 24], queryFn: () => apiClient.monitoring.summary(24), enabled: can("tenant.read"), ...opts });
  const notes: Note[] = [];
  for (const a of approvals.data ?? []) notes.push({ id: `ap-${a.id}`, tone: "warning", title: "Approval required", detail: `${a.tool.replace(/_/g, " ")} · ${a.risk_level.toLowerCase()} risk`, href: "/approvals" });
  for (const h of handoffs.data ?? []) notes.push({ id: `ho-${h.id}`, tone: h.priority === "urgent" ? "danger" : "info", title: "Human handoff requested", detail: `${h.reason.replace(/_/g, " ").toLowerCase()} · ${h.channel}`, href: "/handoff" });
  for (const i of integ.data ?? []) if (i.status === "down" || i.status === "degraded") notes.push({ id: `in-${i.id}`, tone: "danger", title: i.kind === "mcp" ? "MCP server unhealthy" : "Integration disconnected", detail: i.name, href: `/integrations/${i.id}` });
  const denied = mon.data?.policy_decisions?.DENY ?? 0;
  if (denied) notes.push({ id: "pol", tone: "warning", title: "Policy rejections", detail: `${denied} tool call${denied > 1 ? "s" : ""} blocked in the last 24 h`, href: "/audit" });
  if ((mon.data?.authentication.otp_failed ?? 0) >= 3) notes.push({ id: "auth", tone: "warning", title: "Authentication failures", detail: `${mon.data!.authentication.otp_failed} failed OTPs in the last 24 h`, href: "/customers/authentication" });
  if (mon.data && mon.data.tools.failed > 0 && (mon.data.tools.success_rate ?? 1) < 0.9) notes.push({ id: "err", tone: "danger", title: "High tool error rate", detail: `${mon.data.tools.failed} failed calls`, href: "/monitoring" });
  return notes;
}

export function NotificationCenter() {
  const notes = useNotifications();
  return (
    <Popover>
      <PopoverTrigger aria-label={`Notifications${notes.length ? ` (${notes.length})` : ""}`} className="relative rounded-control p-1.5 text-muted hover:bg-hover hover:text-foreground">
        <Bell className="size-4" />
        {notes.length > 0 && <span aria-hidden className="absolute right-1 top-1 size-1.5 rounded-full bg-warning" />}
      </PopoverTrigger>
      <PopoverContent className="w-[22rem] p-0">
        <div className="border-b border-border px-4 py-2.5 text-small font-medium text-strong">Notifications</div>
        {notes.length === 0 ? (
          <p className="px-4 py-6 text-small text-muted">Nothing needs your attention. Approvals, handoffs and integration problems will show up here.</p>
        ) : (
          <ul className="max-h-96 divide-y divide-border overflow-y-auto">
            {notes.map((n) => (
              <li key={n.id}>
                <Link href={n.href} className="flex gap-3 px-4 py-2.5 hover:bg-hover">
                  <StatusDot tone={n.tone} className="mt-1.5" />
                  <span className="min-w-0"><span className="block text-small text-foreground">{n.title}</span><span className="block truncate text-meta text-muted">{n.detail}</span></span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
