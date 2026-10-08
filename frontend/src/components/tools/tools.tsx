"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FlaskConical, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DataTable, type Column } from "@/components/ui/data-table";
import { AUTH_LABEL, RiskBadge } from "@/components/ui/domain-badges";
import { Field, Input } from "@/components/ui/input";
import { KeyValue, PageHeader, Panel, Section } from "@/components/ui/page";
import { Select } from "@/components/ui/select";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusDot } from "@/components/ui/status-dot";
import { Switch } from "@/components/ui/switch";
import { useProfile, useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { AUTH_ORDER, type AuthState, type RiskLevel, type ToolView } from "@/types/domain";
import { formatMs, titleCase } from "@/utils/format";

export const SOURCE_LABEL: Record<ToolView["source"], string> = { builtin: "Platform", rest: "REST API", openapi: "REST / OpenAPI", mcp: "MCP", adapter: "Adapter" };

export function useIntegrationNames() {
  const t = useTenantKey();
  const { can } = useProfile();
  const integ = useQuery({ queryKey: [t, "integrations"], queryFn: apiClient.integrations.list, enabled: can("integration.manage") });
  const mcp = useQuery({ queryKey: [t, "mcp"], queryFn: apiClient.mcp.list, enabled: can("mcp.manage") });
  return (tool: ToolView) => {
    if (tool.source === "builtin") return "Platform";
    const id = tool.integration_id ?? mcp.data?.find((s) => s.id === tool.server_id)?.integration_id;
    return integ.data?.find((i) => i.id === id)?.name ?? (tool.source === "mcp" ? mcp.data?.find((s) => s.id === tool.server_id)?.name : null) ?? "—";
  };
}

export function ToolRegistry() {
  const t = useTenantKey();
  const router = useRouter();
  const q = useQuery({ queryKey: [t, "tools"], queryFn: apiClient.tools.list });
  const name = useIntegrationNames();
  const [risk, setRisk] = useState("all");
  const [source, setSource] = useState("all");
  const [text, setText] = useState("");
  const rows = useMemo(() => (q.data ?? []).filter((x) => (risk === "all" || x.risk_level === risk) && (source === "all" || x.source === source)
    && (!text || x.name.includes(text.toLowerCase()))), [q.data, risk, source, text]);
  const columns: Column<ToolView>[] = [
    { key: "name", header: "Tool", primary: true, cell: (x) => <span className="font-mono text-[12.5px] text-foreground">{x.name}</span>, sort: (x) => x.name },
    { key: "source", header: "Source", cell: (x) => SOURCE_LABEL[x.source] },
    { key: "integration", header: "Integration", cell: (x) => <span className="text-muted">{name(x)}</span>, hideOnMobile: true },
    { key: "risk", header: "Risk", cell: (x) => (x.internal ? <Badge>Internal</Badge> : <RiskBadge level={x.risk_level} />), sort: (x) => ["LOW", "MEDIUM", "HIGH", "CRITICAL"].indexOf(x.risk_level) },
    { key: "auth", header: "Authentication", cell: (x) => <span className="font-mono text-[11.5px] text-muted">{x.min_auth_state}</span> },
    { key: "status", header: "Status", cell: (x) => <StatusDot tone={x.enabled ? "success" : "neutral"} label={x.enabled ? "Active" : "Disabled"} /> },
  ];
  return (
    <PageContainer wide>
      <PageHeader title="Tools" description="Every operation the agent can request, normalised across REST, OpenAPI and MCP. The policy engine decides whether each call runs." />
      <div className="mb-4 flex flex-wrap gap-2">
        <Input aria-label="Search tools" placeholder="Search tools…" value={text} onChange={(e) => setText(e.target.value)} className="max-w-xs" />
        <Select aria-label="Risk" value={risk} onValueChange={setRisk} className="w-40" options={[{ value: "all", label: "Any risk" }, ...["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((r) => ({ value: r, label: titleCase(r) }))]} />
        <Select aria-label="Source" value={source} onValueChange={setSource} className="w-44" options={[{ value: "all", label: "Any source" }, ...Object.entries(SOURCE_LABEL).map(([v, l]) => ({ value: v, label: l }))]} />
      </div>
      <DataTable caption="Tools" rows={q.data ? rows : undefined} error={q.error} onRetry={() => q.refetch()} columns={columns} getKey={(x) => x.name}
        onRowClick={(x) => router.push(`/tools/${x.name}`)} empty={{ title: "No tools match", description: "Connect an integration to add tools." }} />
    </PageContainer>
  );
}

function Requirements({ tool }: { tool: ToolView }) {
  const items: [string, boolean][] = [
    ["Authentication", AUTH_ORDER.indexOf(tool.min_auth_state) > 0],
    ["Strong factor (no voice-only)", tool.risk_level === "HIGH" || tool.risk_level === "CRITICAL"],
    ["Customer confirmation", tool.requires_confirmation || tool.risk_level === "HIGH" || tool.risk_level === "CRITICAL"],
    ["Transaction OTP", tool.min_auth_state === "TRANSACTION_AUTHENTICATED"],
    ["Idempotency key", tool.source === "rest" || tool.source === "openapi"],
    ["Audit event", tool.risk_level !== "LOW"],
  ];
  return (
    <ul className="grid gap-1.5 text-small">
      {items.map(([l, on]) => <li key={l} className={`flex items-center gap-2 ${on ? "text-foreground" : "text-subtle"}`}>{on ? <Check aria-hidden className="size-3.5 text-success" /> : <X aria-hidden className="size-3.5" />}{l}<span className="sr-only">{on ? "required" : "not required"}</span></li>)}
    </ul>
  );
}

export function ToolDetail({ name }: { name: string }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const { can } = useProfile();
  const q = useQuery({ queryKey: [t, "tools"], queryFn: apiClient.tools.list });
  const usage = useQuery({ queryKey: [t, "monitoring", 24], queryFn: () => apiClient.monitoring.summary(24), enabled: can("tenant.read") });
  const integration = useIntegrationNames();
  const tool = q.data?.find((x) => x.name === name);
  const [draft, setDraft] = useState<{ risk_level?: RiskLevel; min_auth_state?: AuthState; requires_confirmation?: boolean; is_enabled?: boolean }>({});
  const [confirmSave, setConfirmSave] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({ mutationFn: () => apiClient.tools.update(name, draft), onSuccess: () => { setDraft({}); void qc.invalidateQueries({ queryKey: [t, "tools"] }); },
    onError: (e) => setError(e instanceof ApiError ? e.userMessage : "Not saved.") });
  if (q.error) return <PageContainer><ErrorState error={q.error} /></PageContainer>;
  if (!q.data) return <PageContainer><SkeletonRows /></PageContainer>;
  if (!tool) return <PageContainer><EmptyState title="Tool not found" description="It may be disabled or belong to another organization." /></PageContainer>;
  const stats = usage.data?.tools.by_tool.find((r) => r.tool === name);
  const editable = can("tool.configure") && tool.source !== "builtin";
  const value = { risk_level: draft.risk_level ?? tool.risk_level, min_auth_state: draft.min_auth_state ?? tool.min_auth_state,
                  requires_confirmation: draft.requires_confirmation ?? tool.requires_confirmation, is_enabled: draft.is_enabled ?? tool.enabled };
  const dirty = Object.keys(draft).length > 0;
  const loweringRisk = draft.risk_level && ["LOW", "MEDIUM", "HIGH", "CRITICAL"].indexOf(draft.risk_level) < ["LOW", "MEDIUM", "HIGH", "CRITICAL"].indexOf(tool.risk_level);
  return (
    <PageContainer>
      <PageHeader title={tool.name} description={tool.description}
        meta={<>{tool.internal ? <Badge>Internal</Badge> : <RiskBadge level={tool.risk_level} />}<StatusDot tone={tool.enabled ? "success" : "neutral"} label={tool.enabled ? "Active" : "Disabled"} /><span>{SOURCE_LABEL[tool.source]} · {integration(tool)}</span></>} />
      <div className="grid gap-8 lg:grid-cols-[1fr_320px]">
        <div className="grid content-start gap-8">
          <Section title="Governance" description="Set by your institution — the model can't change these.">
            <Panel className="grid gap-4 p-4 sm:grid-cols-2">
              <Field label="Risk level" htmlFor="g-risk"><Select id="g-risk" disabled={!editable} value={value.risk_level} onValueChange={(v) => setDraft((d) => ({ ...d, risk_level: v as RiskLevel }))} options={["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((r) => ({ value: r, label: titleCase(r) }))} /></Field>
              <Field label="Minimum authentication" htmlFor="g-auth"><Select id="g-auth" disabled={!editable} value={value.min_auth_state} onValueChange={(v) => setDraft((d) => ({ ...d, min_auth_state: v as AuthState }))} options={AUTH_ORDER.map((a) => ({ value: a, label: AUTH_LABEL[a] }))} /></Field>
              <label className="flex items-center justify-between gap-3 text-small"><span>Always ask the customer to confirm</span><Switch disabled={!editable} checked={value.requires_confirmation} onCheckedChange={(v) => setDraft((d) => ({ ...d, requires_confirmation: v }))} /></label>
              <label className="flex items-center justify-between gap-3 text-small"><span>Available to the agent</span><Switch disabled={!editable || tool.internal} checked={value.is_enabled} onCheckedChange={(v) => setDraft((d) => ({ ...d, is_enabled: v }))} /></label>
              {editable && <div className="flex items-center justify-end gap-2 sm:col-span-2">
                {error && <span role="alert" className="mr-auto text-small text-danger">{error}</span>}
                <Button variant="ghost" disabled={!dirty} onClick={() => setDraft({})}>Discard</Button>
                <Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => setConfirmSave(true)}>Save governance</Button>
              </div>}
              {!editable && <p className="text-meta text-muted sm:col-span-2">{tool.source === "builtin" ? "Platform tools are governed by the platform." : "You need policy management access to change this."}</p>}
            </Panel>
            <ConfirmDialog open={confirmSave} onOpenChange={setConfirmSave} tone={loweringRisk ? "danger" : "primary"} title="Change how this tool is governed?"
              description={loweringRisk ? "You're lowering the risk level. Fewer checks will run before this tool executes on customers' accounts." : "New rules apply to the next call, for chat and voice alike."}
              consequence="The change is recorded in the audit log." confirmLabel="Save changes" onConfirm={() => save.mutateAsync()} />
          </Section>
          <Section title="Input">
            {Object.keys(tool.input_schema.properties ?? {}).length === 0 ? <p className="text-small text-muted">No parameters.</p> : (
              <Panel className="divide-y divide-border">
                {Object.entries(tool.input_schema.properties ?? {}).map(([k, v]) => (
                  <div key={k} className="grid grid-cols-[160px_1fr] gap-3 px-4 py-2.5 text-small">
                    <span className="font-mono text-[12.5px] text-foreground">{k}{tool.input_schema.required?.includes(k) && <span className="text-warning"> *</span>}</span>
                    <span className="text-muted">{v.type}{v.enum ? ` · ${v.enum.join(" | ")}` : ""}{v.description ? ` — ${v.description}` : ""}</span>
                  </div>
                ))}
              </Panel>
            )}
            {tool.injected_params.length > 0 && <p className="text-meta text-muted">Filled by the platform from the verified session (hidden from the model): <span className="font-mono">{tool.injected_params.join(", ")}</span></p>}
          </Section>
          {can("tool.execute") && <ToolTestConsole tool={tool} />}
        </div>
        <aside className="grid content-start gap-6">
          <Section title="Requires"><Panel className="p-4"><Requirements tool={tool} /></Panel></Section>
          <Section title="Execution">
            <Panel className="p-4"><KeyValue items={[
              { label: "Timeout", value: `${tool.timeout_seconds} s` }, { label: "Retries", value: tool.idempotent ? "Up to 2 (idempotent)" : "None (not idempotent)" },
              { label: "Rate limit", value: "Not configured" }, { label: "Calls (24 h)", value: stats?.calls ?? 0 },
              { label: "Failures", value: stats?.failures ?? 0 }, { label: "Blocked", value: stats?.denied ?? 0 }, { label: "p95 latency", value: formatMs(stats?.p95_ms) },
            ]} /></Panel>
          </Section>
          <Link href={`/audit?q=${tool.name}`} className="text-small text-muted underline underline-offset-2">View audit events</Link>
        </aside>
      </div>
    </PageContainer>
  );
}

function ToolTestConsole({ tool }: { tool: ToolView }) {
  const props = tool.input_schema.properties ?? {};
  const [args, setArgs] = useState<Record<string, string>>({});
  const [customer, setCustomer] = useState("CUST1001");
  const [auth, setAuth] = useState<AuthState>("FULLY_AUTHENTICATED");
  const run = useMutation({ mutationFn: () => apiClient.tools.test(tool.name, {
    customer_id: customer, auth_state: auth,
    arguments: Object.fromEntries(Object.entries(args).filter(([, v]) => v !== "").map(([k, v]) => [k, props[k]?.type === "number" || props[k]?.type === "integer" ? Number(v) : v])),
  }) });
  const d = run.data;
  return (
    <Section title="Test" description="Runs through the real gateway and policy engine with a sample customer. State-changing tools stop at the policy step.">
      <Panel className="grid gap-4 p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Sample customer" htmlFor="tt-cust"><Select id="tt-cust" value={customer} onValueChange={setCustomer} options={[{ value: "CUST1001", label: "Priya Sharma (CUST1001)" }, { value: "CUST1002", label: "Arjun Mehta (CUST1002)" }]} /></Field>
          <Field label="Session authentication" htmlFor="tt-auth"><Select id="tt-auth" value={auth} onValueChange={(v) => setAuth(v as AuthState)} options={AUTH_ORDER.map((a) => ({ value: a, label: AUTH_LABEL[a] }))} /></Field>
          {Object.entries(props).filter(([k]) => !tool.injected_params.includes(k)).map(([k, v]) => (
            <Field key={k} label={k} htmlFor={`tt-${k}`}>
              {v.enum ? <Select id={`tt-${k}`} value={args[k] ?? ""} onValueChange={(x) => setArgs((a) => ({ ...a, [k]: x }))} placeholder="Choose…" options={v.enum.map((e) => ({ value: e, label: e }))} />
                : <Input id={`tt-${k}`} inputMode={v.type === "number" ? "decimal" : undefined} value={args[k] ?? ""} onChange={(e) => setArgs((a) => ({ ...a, [k]: e.target.value }))} />}
            </Field>
          ))}
        </div>
        <div><Button onClick={() => run.mutate()} loading={run.isPending}><FlaskConical />Run test</Button></div>
        {run.error && <ErrorState compact error={run.error} />}
        {d && (
          <div className="grid gap-3 rounded-control border border-border bg-background p-3">
            {d.decision && <div className="flex flex-wrap items-center gap-2 text-small"><span className="text-muted">Policy</span>
              <Badge tone={d.decision.decision === "ALLOW" ? "success" : d.decision.decision === "DENY" ? "danger" : "warning"}>{titleCase(d.decision.decision)}</Badge>
              <span className="text-muted">{d.decision.reason}</span><span className="font-mono text-meta text-subtle">risk {d.decision.risk_score} · {d.decision.risk_factors.join(", ")}</span></div>}
            <p className="text-small"><span className="text-muted">Result </span>{d.result.ok ? <span className="text-success">Succeeded in {formatMs(d.result.latency_ms)}</span> : <span className="text-warning">{d.result.error}{d.result.failure_kind ? ` (${d.result.failure_kind.replace("_", " ")})` : ""}</span>}</p>
            {d.result.ok && <pre className="max-h-64 overflow-auto font-mono text-[12px] text-muted">{JSON.stringify(d.result.data, null, 2)}</pre>}
          </div>
        )}
      </Panel>
    </Section>
  );
}
