"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Plus, Upload } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { parse as parseYaml } from "yaml";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { RiskBadge } from "@/components/ui/domain-badges";
import { Field, Input, Textarea } from "@/components/ui/input";
import { KeyValue, PageHeader, Panel, Section } from "@/components/ui/page";
import { Select } from "@/components/ui/select";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { StatusDot, healthTone } from "@/components/ui/status-dot";
import { useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { Integration, RiskLevel, ToolView } from "@/types/domain";
import { formatDate, formatRelative, titleCase } from "@/utils/format";

const CATEGORIES = ["Banking", "Payments", "Cards", "Loans", "Insurance", "KYC", "CRM", "Fraud", "Communication", "Analytics"] as const;

function categoriesFor(tools: ToolView[]): string[] {
  const out = new Set<string>();
  for (const t of tools) {
    if (/card/.test(t.name)) out.add("Cards");
    if (/transfer|payment|pay_/.test(t.name)) out.add("Payments");
    if (/loan|emi/.test(t.name)) out.add("Loans");
    if (/account|balance|transaction/.test(t.name)) out.add("Banking");
    if (/otp|lookup|kyc/.test(t.name)) out.add("KYC");
  }
  return [...out];
}

/** Connectors the platform supports but this tenant hasn't connected. Clicking starts the same add flow. */
const CATALOG: { name: string; category: string; kind: "openapi" | "mcp"; description: string }[] = [
  { name: "Central KYC registry", category: "KYC", kind: "openapi", description: "CKYC search and download for re-KYC." },
  { name: "CRM", category: "CRM", kind: "openapi", description: "Customer profile, cases and service requests." },
  { name: "Fraud risk management", category: "Fraud", kind: "mcp", description: "Real-time fraud scores and case creation." },
  { name: "Insurance policy admin", category: "Insurance", kind: "openapi", description: "Policy status, premiums and claims." },
  { name: "SMS & WhatsApp gateway", category: "Communication", kind: "openapi", description: "Notifications and payment links." },
  { name: "Analytics warehouse", category: "Analytics", kind: "mcp", description: "Read-only portfolio insights for staff." },
];

export function useToolsByIntegration() {
  const t = useTenantKey();
  const tools = useQuery({ queryKey: [t, "tools"], queryFn: apiClient.tools.list });
  const servers = useQuery({ queryKey: [t, "mcp"], queryFn: apiClient.mcp.list });
  return useMemo(() => {
    const map = new Map<string, ToolView[]>();
    for (const tool of tools.data ?? []) {
      const integ = tool.integration_id ?? servers.data?.find((s) => s.id === tool.server_id)?.integration_id;
      if (integ) map.set(integ, [...(map.get(integ) ?? []), tool]);
    }
    return map;
  }, [tools.data, servers.data]);
}

export function AddIntegrationDialog({ open, onOpenChange, preset }: { open: boolean; onOpenChange: (o: boolean) => void; preset?: { name: string; kind: "openapi" | "mcp" } }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const [step, setStep] = useState<"connect" | "import" | "review">("connect");
  const [form, setForm] = useState({ name: preset?.name ?? "", kind: preset?.kind ?? "openapi", base_url: "", auth_type: "api_key", header: "X-API-Key", secret: "", spec_source: "url", spec_url: "", spec_text: "" });
  const [integrationId, setIntegrationId] = useState<string | null>(null);
  const [imported, setImported] = useState<Awaited<ReturnType<typeof apiClient.integrations.importOpenApi>>["imported"]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (v: string) => setForm((x) => ({ ...x, [k]: v }));
  const fail = (e: unknown) => setError(e instanceof ApiError ? (e.detail ? `${e.userMessage} (${e.detail})` : e.userMessage) : "That didn't work.");
  const reset = () => { setStep("connect"); setIntegrationId(null); setImported([]); setSelected(new Set()); setError(null); };

  const connect = async () => {
    setBusy(true); setError(null);
    try {
      const credentials: Record<string, string> | undefined = form.auth_type === "api_key" ? { api_key: form.secret, header: form.header }
        : form.auth_type === "bearer" ? { token: form.secret } : undefined;
      const r = await apiClient.integrations.create({ name: form.name, kind: form.kind, base_url: form.base_url, auth_type: form.auth_type, credentials,
        config: form.spec_url ? { openapi_url: form.spec_url } : {} });
      setIntegrationId(r.id);
      if (form.kind === "mcp") {
        await apiClient.mcp.register({ name: form.name, url: form.base_url, integration_id: r.id, auto_enable: false });
        void qc.invalidateQueries({ queryKey: [t] });
        onOpenChange(false); reset();
      } else setStep("import");
    } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const doImport = async () => {
    setBusy(true); setError(null);
    try {
      let spec: unknown;
      if (form.spec_source === "text") spec = form.spec_text.trim().startsWith("{") ? JSON.parse(form.spec_text) : parseYaml(form.spec_text);
      const r = await apiClient.integrations.importOpenApi(integrationId!, { spec, enable: false });
      setImported(r.imported);
      setSelected(new Set(r.imported.filter((x) => !x.internal && (x.risk_level === "LOW" || x.risk_level === "MEDIUM")).map((x) => x.name)));
      setStep("review");
    } catch (e) { fail(e instanceof SyntaxError ? new ApiError(400, "That isn't valid JSON or YAML.", null) : e); } finally { setBusy(false); }
  };
  const activate = async () => {
    setBusy(true); setError(null);
    try {
      for (const name of selected) await apiClient.tools.update(name, { is_enabled: true });
      void qc.invalidateQueries({ queryKey: [t] });
      onOpenChange(false); reset();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };
  const counts = imported.reduce<Record<RiskLevel, number>>((acc, x) => ({ ...acc, [x.risk_level]: acc[x.risk_level] + 1 }), { LOW: 0, MEDIUM: 0, HIGH: 0, CRITICAL: 0 });

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <DialogContent title={step === "connect" ? "Add integration" : step === "import" ? "Import OpenAPI" : "Review generated tools"} className="max-w-2xl"
        description={step === "review" ? "Tools stay disabled until you activate them. High-risk tools always need confirmation; critical ones need transaction authentication." : undefined}>
        {step === "connect" && (
          <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void connect(); }}>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Name" htmlFor="i-name"><Input id="i-name" required value={form.name} onChange={(e) => set("name")(e.target.value)} placeholder="Core banking API" /></Field>
              <Field label="Type" htmlFor="i-kind"><Select id="i-kind" value={form.kind} onValueChange={set("kind")} options={[{ value: "openapi", label: "REST / OpenAPI" }, { value: "mcp", label: "MCP server" }]} /></Field>
            </div>
            <Field label={form.kind === "mcp" ? "MCP endpoint (Streamable HTTP)" : "Base URL"} htmlFor="i-url"><Input id="i-url" type="url" required value={form.base_url} onChange={(e) => set("base_url")(e.target.value)} placeholder="https://api.bank.internal" /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Authentication" htmlFor="i-auth"><Select id="i-auth" value={form.auth_type} onValueChange={set("auth_type")} options={[{ value: "api_key", label: "API key" }, { value: "bearer", label: "Bearer token" }, { value: "none", label: "None (mTLS at network)" }]} /></Field>
              {form.auth_type === "api_key" && <Field label="Header" htmlFor="i-header"><Input id="i-header" value={form.header} onChange={(e) => set("header")(e.target.value)} /></Field>}
            </div>
            {form.auth_type !== "none" && <Field label={form.auth_type === "api_key" ? "API key" : "Token"} htmlFor="i-secret" hint="Encrypted at rest and never shown again. You can use env:VAR_NAME to reference a secret.">
              <Input id="i-secret" type="password" autoComplete="off" required value={form.secret} onChange={(e) => set("secret")(e.target.value)} /></Field>}
            {error && <p role="alert" className="text-small text-danger">{error}</p>}
            <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button type="submit" variant="primary" loading={busy}>{form.kind === "mcp" ? "Connect and discover tools" : "Connect"}</Button></div>
          </form>
        )}
        {step === "import" && (
          <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); void doImport(); }}>
            <div role="radiogroup" aria-label="Specification source" className="flex gap-2">
              {[["url", "From the API (openapi.json)"], ["text", "Paste or upload YAML / JSON"]].map(([v, l]) => (
                <button type="button" key={v} role="radio" aria-checked={form.spec_source === v} onClick={() => set("spec_source")(v)} className={`rounded-control border px-3 py-1.5 text-small ${form.spec_source === v ? "border-strong text-strong" : "border-border-strong text-muted"}`}>{l}</button>
              ))}
            </div>
            {form.spec_source === "text" ? <>
              <input type="file" accept=".json,.yaml,.yml" aria-label="Upload specification" onChange={async (e) => { const f = e.target.files?.[0]; if (f) set("spec_text")(await f.text()); }} className="text-small text-muted" />
              <Textarea aria-label="Specification" value={form.spec_text} onChange={(e) => set("spec_text")(e.target.value)} className="min-h-48 font-mono text-[12px]" placeholder="openapi: 3.1.0 …" />
            </> : <p className="text-small text-muted">The specification is fetched from {form.spec_url || `${form.base_url || "the base URL"}/openapi.json`} by the backend.</p>}
            {error && <p role="alert" className="text-small text-danger">{error}</p>}
            <div className="flex justify-end"><Button type="submit" variant="primary" loading={busy}><Upload />Import endpoints</Button></div>
          </form>
        )}
        {step === "review" && (
          <div className="grid gap-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {(["LOW", "MEDIUM", "HIGH", "CRITICAL"] as RiskLevel[]).map((r) => <Panel key={r} className="px-3 py-2"><p className="text-meta text-muted">{titleCase(r)}</p><p className="tabular text-section text-strong">{counts[r]}</p></Panel>)}
            </div>
            <p className="text-small text-muted">✓ {imported.length} endpoints discovered · ✓ {imported.length} tools generated · {imported.filter((x) => x.internal).length} internal (platform-only)</p>
            <ul className="max-h-72 divide-y divide-border overflow-y-auto rounded-panel border border-border">
              {imported.map((x) => (
                <li key={x.name} className="flex items-start gap-3 px-3 py-2.5">
                  <input type="checkbox" aria-label={`Activate ${x.name}`} disabled={x.internal} checked={selected.has(x.name)} className="mt-1 accent-white"
                    onChange={(e) => setSelected((s) => { const n = new Set(s); if (e.target.checked) n.add(x.name); else n.delete(x.name); return n; })} />
                  <div className="min-w-0 flex-1"><p className="font-mono text-[12.5px] text-foreground">{x.name}</p><p className="truncate text-meta text-muted">{x.description}</p></div>
                  {x.internal ? <Badge>Internal</Badge> : <RiskBadge level={x.risk_level} />}
                </li>
              ))}
            </ul>
            {error && <p role="alert" className="text-small text-danger">{error}</p>}
            <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => { onOpenChange(false); reset(); }}>Keep all disabled</Button><Button variant="primary" loading={busy} onClick={activate}>Activate {selected.size} tool{selected.size === 1 ? "" : "s"}</Button></div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function IntegrationsMarketplace() {
  const t = useTenantKey();
  const q = useQuery({ queryKey: [t, "integrations"], queryFn: apiClient.integrations.list });
  const byIntegration = useToolsByIntegration();
  const [add, setAdd] = useState<{ name: string; kind: "openapi" | "mcp" } | null | undefined>(undefined);
  const [cat, setCat] = useState<string>("All");
  const connected = (q.data ?? []).map((i) => ({ i, tools: byIntegration.get(i.id) ?? [], cats: categoriesFor(byIntegration.get(i.id) ?? []) }));
  const catalog = CATALOG.filter((c) => cat === "All" || c.category === cat);
  return (
    <PageContainer wide>
      <PageHeader title="Integrations" description="Bank systems the agent can reach. The platform never connects to a database directly — only APIs and MCP servers."
        actions={<Button variant="primary" onClick={() => setAdd(null)}><Plus />Add integration</Button>} />
      <div role="group" aria-label="Category" className="mb-5 flex flex-wrap gap-1.5">
        {["All", ...CATEGORIES].map((c) => <button key={c} aria-pressed={cat === c} onClick={() => setCat(c)} className={`rounded-full border px-3 py-1 text-small ${cat === c ? "border-strong text-strong" : "border-border-strong text-muted hover:text-foreground"}`}>{c}</button>)}
      </div>
      {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <SkeletonRows /> : (
        <div className="grid gap-8">
          <Section title="Connected">
            {connected.filter((c) => cat === "All" || c.cats.includes(cat)).length === 0 ? <EmptyState title="Nothing connected in this category" description="Connect a REST/OpenAPI service or an MCP server to give the agent new capabilities." /> : (
              <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {connected.filter((c) => cat === "All" || c.cats.includes(cat)).map(({ i, tools, cats }) => (
                  <li key={i.id}>
                    <Link href={`/integrations/${i.id}`} className="grid h-full gap-3 rounded-panel border border-border bg-surface p-4 hover:border-border-strong hover:bg-hover">
                      <div className="flex items-start justify-between gap-2"><p className="text-body font-medium text-strong">{i.name}</p><StatusDot tone={i.status === "unknown" ? "success" : healthTone(i.status)} label={i.status === "unknown" ? "Connected" : titleCase(i.status)} /></div>
                      <KeyValue items={[
                        { label: "Type", value: i.kind === "mcp" ? "MCP server" : "REST / OpenAPI" },
                        { label: "Authentication", value: titleCase(i.auth_type) },
                        { label: "Health", value: i.last_checked_at ? `${titleCase(i.status)} · ${formatRelative(i.last_checked_at)}` : "Not checked yet" },
                        { label: "Tools", value: tools.length },
                      ]} />
                      <div className="flex flex-wrap gap-1">{cats.map((c) => <Badge key={c}>{c}</Badge>)}</div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Section>
          <Section title="Available connectors" description="Supported integrations not yet connected for this organization.">
            <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {catalog.map((c) => (
                <li key={c.name} className="grid gap-2 rounded-panel border border-dashed border-border-strong p-4">
                  <div className="flex items-center justify-between"><p className="text-body text-foreground">{c.name}</p><Badge>{c.category}</Badge></div>
                  <p className="text-small text-muted">{c.description}</p>
                  <div><Button size="sm" onClick={() => setAdd({ name: c.name, kind: c.kind })}>Connect</Button></div>
                </li>
              ))}
            </ul>
          </Section>
        </div>
      )}
      <AddIntegrationDialog open={add !== undefined} onOpenChange={(o) => !o && setAdd(undefined)} preset={add ?? undefined} />
    </PageContainer>
  );
}

export function IntegrationDetail({ id }: { id: string }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: [t, "integrations"], queryFn: apiClient.integrations.list });
  const byIntegration = useToolsByIntegration();
  const test = useMutation({ mutationFn: () => apiClient.integrations.test(id), onSuccess: () => qc.invalidateQueries({ queryKey: [t, "integrations"] }) });
  const i = q.data?.find((x) => x.id === id);
  const tools = byIntegration.get(id) ?? [];
  if (!q.data) return <PageContainer><SkeletonRows /></PageContainer>;
  if (!i) return <PageContainer><EmptyState title="Integration not found" description="It may belong to another organization." /></PageContainer>;
  return (
    <PageContainer>
      <PageHeader title={i.name} description={i.kind === "mcp" ? "MCP server" : "REST / OpenAPI service"}
        meta={<StatusDot tone={healthTone(i.status)} label={titleCase(i.status)} />}
        actions={<Button onClick={() => test.mutate()} loading={test.isPending}><Activity />Test connection</Button>} />
      {test.data && <p role="status" className={`mb-6 text-small ${test.data.ok ? "text-success" : "text-danger"}`}>{test.data.ok ? "Connection healthy" : "Connection failed"} · {test.data.detail} · {Math.round(test.data.latency_ms)} ms</p>}
      <div className="grid gap-8 lg:grid-cols-[320px_1fr]">
        <Section title="Connection">
          <Panel className="p-4"><KeyValue items={[
            { label: "Endpoint", value: i.base_url ?? "—", mono: true }, { label: "Authentication", value: titleCase(i.auth_type) },
            { label: "Credentials", value: i.auth_type === "none" ? "None" : "Stored encrypted · never displayed" },
            { label: "Last checked", value: i.last_checked_at ? formatDate(i.last_checked_at) : "Never" }, { label: "Enabled", value: i.is_enabled ? "Yes" : "No" },
          ]} /></Panel>
        </Section>
        <Section title={`Tools (${tools.length})`}>
          <IntegrationTools tools={tools} />
        </Section>
      </div>
    </PageContainer>
  );
}

function IntegrationTools({ tools }: { tools: ToolView[] }) {
  if (!tools.length) return <EmptyState title="No tools yet" description="Import an OpenAPI specification or discover MCP tools to expose operations to the agent." />;
  return (
    <ul className="divide-y divide-border rounded-panel border border-border">
      {tools.map((x) => (
        <li key={x.name}><Link href={`/tools/${x.name}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-hover">
          <span className="min-w-0 flex-1"><span className="block font-mono text-[12.5px] text-foreground">{x.name}</span><span className="block truncate text-meta text-muted">{x.description}</span></span>
          {x.internal ? <Badge>Internal</Badge> : <RiskBadge level={x.risk_level} />}
          <StatusDot tone={x.enabled ? "success" : "neutral"} label={x.enabled ? "Active" : "Disabled"} />
        </Link></li>
      ))}
    </ul>
  );
}

export function CredentialsView() {
  const t = useTenantKey();
  const q = useQuery({ queryKey: [t, "integrations"], queryFn: apiClient.integrations.list });
  return (
    <PageContainer>
      <PageHeader title="Credentials" description="How each integration authenticates. Secret values are encrypted at rest and can't be viewed — only replaced." />
      {q.error ? <ErrorState error={q.error} /> : !q.data ? <SkeletonRows /> : (
        <ul className="divide-y divide-border rounded-panel border border-border">
          {q.data.map((i: Integration) => (
            <li key={i.id} className="flex flex-wrap items-center gap-4 px-4 py-3">
              <Link href={`/integrations/${i.id}`} className="min-w-0 flex-1 text-small text-foreground hover:underline">{i.name}</Link>
              <span className="text-small text-muted">{titleCase(i.auth_type)}</span>
              <span className="font-mono text-[12px] text-subtle">{i.auth_type === "none" ? "—" : "••••••••••••"}</span>
              <Badge tone="neutral">Encrypted</Badge>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-4 text-small text-muted">Rotate a secret by updating the integration. Production deployments should reference a secret manager (for example env: or vault: references) instead of storing values here.</p>
    </PageContainer>
  );
}
