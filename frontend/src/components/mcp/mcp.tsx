"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { AddIntegrationDialog } from "@/components/integrations/integrations";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AUTH_LABEL, RiskBadge } from "@/components/ui/domain-badges";
import { KeyValue, PageHeader, Panel, Section } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusDot, healthTone } from "@/components/ui/status-dot";
import { useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { formatMs, formatRelative, titleCase } from "@/utils/format";

const statusLabel = (s: string) => (s === "unknown" ? "Connected" : titleCase(s));

export function McpServers() {
  const t = useTenantKey();
  const q = useQuery({ queryKey: [t, "mcp"], queryFn: apiClient.mcp.list, refetchInterval: 30_000 });
  const [add, setAdd] = useState(false);
  return (
    <PageContainer>
      <PageHeader title="MCP servers" description="Model Context Protocol servers exposing bank operations. Discovered tools start disabled until reviewed."
        actions={<Button variant="primary" onClick={() => setAdd(true)}><Plus />Register server</Button>} />
      {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <SkeletonRows /> : q.data.length === 0
        ? <EmptyState title="No MCP servers" description="Register an MCP server to expose card, payment or fraud operations as governed tools." action={<Button onClick={() => setAdd(true)}><Plus />Register server</Button>} />
        : (
        <ul className="divide-y divide-border rounded-panel border border-border">
          {q.data.map((s) => (
            <li key={s.id}><Link href={`/mcp/${s.id}`} className="grid gap-1 px-4 py-3 hover:bg-hover sm:grid-cols-[1fr_auto_auto_auto] sm:items-center sm:gap-6">
              <span><span className="block text-body font-medium text-foreground">{s.name}</span><span className="block font-mono text-meta text-muted">{s.url}</span></span>
              <span className="text-small text-muted">{s.tool_count} tools</span>
              <span className="text-small text-muted">{s.protocol_version ?? "—"}</span>
              <StatusDot tone={s.status === "unknown" ? "success" : healthTone(s.status)} label={statusLabel(s.status)} />
            </Link></li>
          ))}
        </ul>
      )}
      <AddIntegrationDialog open={add} onOpenChange={setAdd} preset={{ name: "", kind: "mcp" }} />
    </PageContainer>
  );
}

export function McpServerDetail({ id }: { id: string }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const servers = useQuery({ queryKey: [t, "mcp"], queryFn: apiClient.mcp.list });
  const tools = useQuery({ queryKey: [t, "mcp", id, "tools"], queryFn: () => apiClient.mcp.tools(id) });
  const usage = useQuery({ queryKey: [t, "monitoring", 24], queryFn: () => apiClient.monitoring.summary(24) });
  const activity = useQuery({ queryKey: [t, "activity", 100], queryFn: () => apiClient.monitoring.activity(100) });
  const discover = useMutation({ mutationFn: () => apiClient.mcp.discover(id), onSuccess: () => qc.invalidateQueries({ queryKey: [t, "mcp"] }) });
  const s = servers.data?.find((x) => x.id === id);
  if (!servers.data) return <PageContainer><SkeletonRows /></PageContainer>;
  if (!s) return <PageContainer><EmptyState title="Server not found" description="It may belong to another organization." /></PageContainer>;
  const names = new Set((tools.data ?? []).map((x) => x.name));
  const stats = (usage.data?.tools.by_tool ?? []).filter((r) => names.has(r.tool));
  const calls = stats.reduce((a, r) => a + r.calls, 0);
  const errors = stats.reduce((a, r) => a + r.failures, 0);
  const last = activity.data?.find((a) => a.kind === "tool" && names.has(a.title));
  return (
    <PageContainer>
      <PageHeader title={s.name} description={`${s.server_info.name ?? "MCP server"} ${s.server_info.version ?? ""}`}
        meta={<StatusDot tone={s.status === "unknown" ? "success" : healthTone(s.status)} label={statusLabel(s.status)} />}
        actions={<Button onClick={() => discover.mutate()} loading={discover.isPending}><RefreshCw />Rediscover tools</Button>} />
      <div className="grid gap-8 lg:grid-cols-[320px_1fr]">
        <div className="grid content-start gap-6">
          <Section title="Server"><Panel className="p-4"><KeyValue items={[
            { label: "Endpoint", value: s.url ?? "—", mono: true }, { label: "Transport", value: titleCase(s.transport) },
            { label: "Authentication", value: titleCase(s.auth_type) }, { label: "Protocol", value: s.protocol_version ?? "—", mono: true },
            { label: "Discovered", value: s.last_discovered_at ? formatRelative(s.last_discovered_at) : "Never" },
          ]} /></Panel></Section>
          <Section title="Health (24 h)"><Panel className="p-4"><KeyValue items={[
            { label: "Calls", value: calls }, { label: "Errors", value: <span className={errors ? "text-danger" : ""}>{errors}</span> },
            { label: "p95 latency", value: formatMs(Math.max(0, ...stats.map((r) => r.p95_ms ?? 0)) || null) },
            { label: "Last invocation", value: last ? `${last.title} · ${formatRelative(last.at)}` : "None yet" },
          ]} /></Panel></Section>
        </div>
        <Section title={`Tools (${tools.data?.length ?? "…"})`}>
          {tools.error ? <ErrorState error={tools.error} /> : !tools.data ? <SkeletonRows /> : (
            <ul className="grid gap-3">
              {tools.data.map((x) => (
                <li key={x.id}><Panel className="grid gap-3 p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Link href={`/tools/${x.name}`} className="font-mono text-[13px] text-strong hover:underline">{x.name}</Link>
                    <div className="flex items-center gap-2"><RiskBadge level={x.risk_level} /><StatusDot tone={x.is_enabled ? "success" : "neutral"} label={x.is_enabled ? "Active" : "Disabled"} /></div>
                  </div>
                  <p className="text-small text-muted">{x.description}</p>
                  <KeyValue items={[
                    { label: "Authentication", value: AUTH_LABEL[x.min_auth_state] }, { label: "Confirmation", value: x.requires_confirmation ? "Required" : "Not required" },
                    { label: "Permissions", value: x.annotations.readOnlyHint ? "Read-only" : x.annotations.destructiveHint ? "Changes accounts (destructive)" : "Writes data" },
                    { label: "Injected", value: Object.keys(x.injected_params).join(", ") || "—", mono: true },
                  ]} />
                  {x.annotations.destructiveHint === true && <Badge tone="warning" className="w-fit">Server marks this destructive</Badge>}
                </Panel></li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </PageContainer>
  );
}
