"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Monitor, Plus, X } from "lucide-react";
import { useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input } from "@/components/ui/input";
import { KeyValue, Kbd, PageHeader, Panel, SampleDataNote, Section } from "@/components/ui/page";
import { ErrorState, SkeletonRows } from "@/components/ui/states";
import { ENVIRONMENT } from "@/config/env";
import { useProfile, useTenant, useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import { ROLE_LABELS, ROLE_PERMISSIONS, type BackendPermission } from "@/lib/permissions";
import { LANGUAGE_NAMES, formatRelative } from "@/utils/format";
import { useSignOut } from "./topbar";

export function SettingsView() {
  const { profile } = useProfile();
  const devices = useQuery({ queryKey: ["devices"], queryFn: apiClient.auth.deviceSessions });
  const signOut = useSignOut();
  return (
    <PageContainer>
      <PageHeader title="Settings" description="Your account, security and accessibility." />
      <div className="grid gap-8 lg:grid-cols-2">
        <Section title="Account"><Panel className="p-4"><KeyValue items={[
          { label: "Email", value: profile?.email ?? "—" }, { label: "Roles", value: profile?.roles.map((r) => ROLE_LABELS[r] ?? r).join(", ") ?? "—" },
          { label: "Session ends", value: profile ? new Date(profile.expires_at * 1000).toLocaleTimeString() : "—" }, { label: "Environment", value: ENVIRONMENT },
        ]} /></Panel></Section>
        <Section title="Security">
          <Panel className="grid gap-3 p-4 text-small">
            <p className="flex items-center gap-2"><Check aria-hidden className="size-4 text-success" />Two-step sign-in is required for every staff account (simulated in this demo — the backend has no staff MFA endpoint yet).</p>
            <p className="flex items-center gap-2"><Check aria-hidden className="size-4 text-success" />Your session token is held in a secure, HTTP-only cookie and isn&apos;t readable by page scripts.</p>
            <p className="flex items-center gap-2"><Check aria-hidden className="size-4 text-success" />You&apos;re signed out after 15 minutes of inactivity.</p>
            <div><Button variant="danger-outline" onClick={signOut}>Sign out</Button></div>
          </Panel>
        </Section>
        <Section title="Devices and sessions" actions={<SampleDataNote what="Device sessions" />}>
          <Panel className="divide-y divide-border">{(devices.data ?? []).map((d) => (
            <div key={d.id} className="flex items-center gap-3 p-4 text-small"><Monitor aria-hidden className="size-4 text-subtle" /><span className="flex-1">{d.device}</span>{d.current && <Badge tone="success">This device</Badge>}<span className="text-meta text-muted">{formatRelative(d.lastActive)}</span></div>
          ))}</Panel>
        </Section>
        <Section title="Keyboard & accessibility">
          <Panel className="grid gap-2 p-4 text-small">
            <p className="flex justify-between"><span>Search and commands</span><Kbd>Ctrl K</Kbd></p>
            <p className="flex justify-between"><span>Send message</span><Kbd>Enter</Kbd></p>
            <p className="flex justify-between"><span>New line</span><Kbd>Shift Enter</Kbd></p>
            <p className="text-muted">Motion follows your system&apos;s reduced-motion setting. All controls are reachable by keyboard and labelled for screen readers.</p>
          </Panel>
        </Section>
      </div>
    </PageContainer>
  );
}

export function OrganizationView() {
  const tenant = useTenant();
  const t = tenant.data;
  return (
    <PageContainer>
      <PageHeader title="Organization" description="Everything in the console — customers, documents, tools, conversations, policies and logs — is isolated to this organization." />
      {tenant.error ? <ErrorState error={tenant.error} /> : !t ? <SkeletonRows /> : (
        <Panel className="max-w-2xl p-4"><KeyValue items={[
          { label: "Name", value: t.name }, { label: "Organization ID", value: t.slug, mono: true }, { label: "Type", value: t.institution_type.toUpperCase() },
          { label: "Default language", value: LANGUAGE_NAMES[t.default_language]?.english ?? t.default_language },
          { label: "Languages", value: t.supported_languages.map((l) => LANGUAGE_NAMES[l]?.native ?? l).join(" · ") }, { label: "Status", value: t.is_active ? "Active" : "Suspended" },
        ]} /></Panel>
      )}
    </PageContainer>
  );
}

export function UsersView() {
  const t = useTenantKey();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: [t, "users"], queryFn: apiClient.users.list });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "human_agent" });
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({ mutationFn: () => apiClient.users.create({ email: form.email, full_name: form.full_name, password: form.password, roles: [form.role] }),
    onSuccess: () => { setOpen(false); setForm({ email: "", full_name: "", password: "", role: "human_agent" }); void qc.invalidateQueries({ queryKey: [t, "users"] }); },
    onError: (e) => setError(e instanceof ApiError ? (e.detail ?? e.userMessage) : "Not created.") });
  return (
    <PageContainer>
      <PageHeader title="Users" description="Staff who can sign in to this organization's console." actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus />Invite user</Button>} />
      {q.error ? <ErrorState error={q.error} /> : !q.data ? <SkeletonRows /> : (
        <ul className="divide-y divide-border rounded-panel border border-border">{q.data.map((u) => (
          <li key={u.id} className="flex flex-wrap items-center gap-3 px-4 py-3"><span className="flex-1 text-small">{u.email}<span className="block text-meta text-muted">{u.full_name}</span></span>
            {u.roles.map((r) => <Badge key={r}>{ROLE_LABELS[r] ?? r}</Badge>)}<Badge tone={u.is_active ? "success" : "neutral"}>{u.is_active ? "Active" : "Inactive"}</Badge></li>
        ))}</ul>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title="Invite user" description="They'll set up two-step sign-in on first login.">
          <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); setError(null); create.mutate(); }}>
            <Field label="Work email" htmlFor="u-email"><Input id="u-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Full name" htmlFor="u-name"><Input id="u-name" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
            <Field label="Temporary password" htmlFor="u-pass" hint="At least 12 characters"><Input id="u-pass" type="password" autoComplete="new-password" minLength={12} required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
            <Field label="Role" htmlFor="u-role"><select id="u-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className="h-8 rounded-control border border-border-strong bg-surface px-2 text-body">
              {Object.keys(ROLE_PERMISSIONS).filter((r) => r !== "platform_admin").map((r) => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}</select></Field>
            {error && <p role="alert" className="text-small text-danger">{error}</p>}
            <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" variant="primary" loading={create.isPending}>Invite</Button></div>
          </form>
        </DialogContent>
      </Dialog>
    </PageContainer>
  );
}

const PERM_LABEL: Record<BackendPermission, string> = {
  "tenant:admin": "Administer organization", "tenant:read": "View organization", "user:manage": "Manage users", "agent:manage": "Configure agents", "agent:read": "View agents & tools",
  "knowledge:manage": "Manage documents", "knowledge:read": "Search knowledge", "integration:manage": "Manage integrations & MCP", "tool:test": "Test tools",
  "policy:manage": "Manage policies & tool governance", "audit:read": "Read audit logs", "conversation:read": "Read conversations & customers",
  "handoff:handle": "Handle handoffs", "approval:decide": "Approve high-value actions", "session:create": "Create customer sessions", "evaluation:run": "Run evaluations",
};

export function RolesView() {
  const roles = Object.keys(ROLE_PERMISSIONS).filter((r) => r !== "platform_admin");
  return (
    <PageContainer wide>
      <PageHeader title="Roles" description="What each role can do. The backend enforces the same matrix on every request; administrators can't approve money movement (maker-checker)." />
      <div className="overflow-x-auto rounded-panel border border-border">
        <table className="w-full min-w-[760px] text-small">
          <caption className="sr-only">Role permissions</caption>
          <thead><tr className="border-b border-border text-left"><th className="px-3 py-2 font-medium text-muted">Permission</th>{roles.map((r) => <th key={r} className="px-2 py-2 text-center font-medium text-muted">{ROLE_LABELS[r]}</th>)}</tr></thead>
          <tbody>{(Object.keys(PERM_LABEL) as BackendPermission[]).map((p) => (
            <tr key={p} className="border-b border-border last:border-0"><td className="px-3 py-2">{PERM_LABEL[p]}</td>
              {roles.map((r) => <td key={r} className="px-2 py-2 text-center">{ROLE_PERMISSIONS[r].includes(p) ? <Check aria-label="Allowed" className="mx-auto size-4 text-success" /> : <X aria-label="Not allowed" className="mx-auto size-3.5 text-subtle" />}</td>)}</tr>
          ))}</tbody>
        </table>
      </div>
      <p className="mt-3 text-meta text-muted">Custom roles need a backend role-management endpoint.</p>
    </PageContainer>
  );
}
