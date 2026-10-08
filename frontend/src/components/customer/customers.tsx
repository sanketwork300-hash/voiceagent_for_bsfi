"use client";
import { useQuery } from "@tanstack/react-query";
import { Eye, EyeOff } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { AuthLadder } from "@/components/authentication/auth-ladder";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DataTable, type Column } from "@/components/ui/data-table";
import { AuthBadge } from "@/components/ui/domain-badges";
import { Input } from "@/components/ui/input";
import { KeyValue, PageHeader, Panel, SampleDataNote, Section } from "@/components/ui/page";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { StatusDot, healthTone } from "@/components/ui/status-dot";
import { useProfile } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import type { CustomerRecord } from "@/lib/api/mock/customers";
import { formatDate, formatINR, titleCase } from "@/utils/format";

export function CustomerList() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["customers", q], queryFn: () => apiClient.customers.list(q) });
  const columns: Column<CustomerRecord>[] = [
    { key: "name", header: "Customer", primary: true, cell: (c) => <span className="font-medium">{c.name}</span>, sort: (c) => c.name },
    { key: "id", header: "Customer ID", cell: (c) => <span className="font-mono text-[12.5px]">{c.id}</span> },
    { key: "segment", header: "Segment", cell: (c) => c.segment },
    { key: "kyc", header: "KYC", cell: (c) => <StatusDot tone={c.kycStatus === "Verified" ? "success" : "warning"} label={c.kycStatus} /> },
    { key: "auth", header: "Last verification", cell: (c) => <AuthBadge state={c.lastAuth.state} /> },
    { key: "risk", header: "Risk signals", cell: (c) => (c.riskSignals.length ? <Badge tone="warning">{c.riskSignals.length}</Badge> : <span className="text-muted">None</span>) },
  ];
  return (
    <PageContainer>
      <PageHeader title="Customers" description="Customer 360 for supervisors and agents. Sensitive fields are masked by default." meta={<SampleDataNote what="Customer records" />} />
      <Input aria-label="Search customers" placeholder="Search by name or customer ID" value={q} onChange={(e) => setQ(e.target.value)} className="mb-4 max-w-xs" />
      <DataTable caption="Customers" rows={list.data} loading={list.isLoading} columns={columns} getKey={(c) => c.id} onRowClick={(c) => router.push(`/customers/${c.id}`)}
        empty={{ title: "No customers match", description: "Try a different name or ID. Customer data comes from the bank's systems." }} />
    </PageContainer>
  );
}

export function CustomerDetail({ id }: { id: string }) {
  const { can } = useProfile();
  const q = useQuery({ queryKey: ["customer", id], queryFn: () => apiClient.customers.get(id) });
  const [revealed, setRevealed] = useState(false);
  const [askReveal, setAskReveal] = useState(false);
  const c = q.data;
  if (q.isLoading) return <PageContainer><SkeletonRows rows={8} /></PageContainer>;
  if (!c) return <PageContainer><EmptyState title="Customer not found" description="This customer doesn't exist in this organization, or you don't have access." /></PageContainer>;
  const canReveal = can("customer.update");
  return (
    <PageContainer>
      <PageHeader title={c.name} description={`${c.segment} · customer since ${c.customerSince}`}
        meta={<><StatusDot tone={c.kycStatus === "Verified" ? "success" : "warning"} label={c.kycStatus === "Verified" ? "Verified customer" : c.kycStatus} /><SampleDataNote what="Customer 360" /></>}
        actions={<>
          <Button asChild><Link href="/chat">Open test chat</Link></Button>
          {canReveal && (revealed
            ? <Button variant="ghost" onClick={() => setRevealed(false)}><EyeOff />Mask details</Button>
            : <Button variant="ghost" onClick={() => setAskReveal(true)}><Eye />Reveal contact details</Button>)}
        </>} />
      <ConfirmDialog open={askReveal} onOpenChange={setAskReveal} tone="primary" title="Reveal sensitive details?"
        description="Only reveal contact details when the customer's request needs it. Reveals should be audit-logged by the customer API, which isn't connected in this demo."
        confirmLabel="Reveal" onConfirm={() => setRevealed(true)} />
      <div className="grid gap-8 lg:grid-cols-3">
        <Section title="Identity">
          <Panel className="p-4">
            <KeyValue items={[
              { label: "Customer ID", value: c.id, mono: true },
              { label: "Phone", value: revealed ? `${c.phoneMasked} (full number from bank CRM)` : c.phoneMasked },
              { label: "Email", value: c.emailMasked },
              { label: "Customer since", value: c.customerSince },
              { label: "KYC", value: c.kycStatus },
            ]} />
            {revealed && <p className="mt-3 text-meta text-warning">Full values are fetched from the bank&apos;s CRM, which isn&apos;t connected in this demo.</p>}
          </Panel>
        </Section>
        <Section title="Authentication">
          <Panel className="p-4">
            <AuthLadder state={c.lastAuth.state} />
            <p className="mt-3 text-meta text-muted">Last: {c.lastAuth.method} · {formatDate(c.lastAuth.at)}</p>
          </Panel>
        </Section>
        <Section title="Risk signals">
          <Panel className="p-4">
            {c.riskSignals.length === 0 ? <p className="text-small text-muted">No open risk signals.</p> : (
              <ul className="grid gap-2">{c.riskSignals.map((r) => <li key={r.label} className="flex items-center justify-between text-small"><StatusDot tone={r.level === "high" ? "danger" : r.level === "medium" ? "warning" : "neutral"} label={r.label} /><span className="text-meta text-muted">{formatDate(r.at)}</span></li>)}</ul>
            )}
          </Panel>
        </Section>
        <Section title="Accounts" className="lg:col-span-1">
          <Panel className="divide-y divide-border">
            {c.accounts.map((a) => <div key={a.numberMasked} className="flex items-center justify-between gap-3 p-4"><div><p className="text-small">{a.type} {a.numberMasked}</p><p className="text-meta text-muted">{a.branch}</p></div><p className="tabular font-mono text-body text-strong">{formatINR(a.available)}</p></div>)}
          </Panel>
        </Section>
        <Section title="Cards">
          <Panel className="divide-y divide-border">
            {c.cards.map((k) => <div key={k.numberMasked} className="grid gap-1 p-4"><div className="flex justify-between"><p className="text-small">{k.network} {titleCase(k.type)} {k.numberMasked}</p><StatusDot tone={healthTone(k.status)} label={titleCase(k.status)} /></div>
              {k.outstanding !== undefined && <p className="text-meta text-muted">Outstanding {formatINR(k.outstanding)} of {formatINR(k.limit)} · due {formatDate(k.dueDate)}</p>}</div>)}
          </Panel>
        </Section>
        <Section title="Loans">
          <Panel className="divide-y divide-border">
            {c.loans.length === 0 ? <p className="p-4 text-small text-muted">No loans.</p> : c.loans.map((l) => <div key={l.numberMasked} className="grid gap-1 p-4"><div className="flex justify-between"><p className="text-small">{l.type} {l.numberMasked}</p><p className="tabular font-mono text-strong">{formatINR(l.outstanding)}</p></div><p className="text-meta text-muted">EMI {formatINR(l.emi)} on {formatDate(l.nextEmi)} · {l.rate}% · {l.tenureLeft} months left</p></div>)}
          </Panel>
        </Section>
        <Section title="Recent transactions" className="lg:col-span-2">
          <DataTable caption="Transactions" rows={c.transactions} getKey={(x) => x.id} pageSize={10}
            empty={{ title: "No recent transactions", description: "Transactions from the bank's core system appear here." }}
            columns={[
              { key: "d", header: "Date", cell: (x) => formatDate(x.date), sort: (x) => x.date },
              { key: "desc", header: "Description", primary: true, cell: (x) => x.description },
              { key: "ch", header: "Channel", cell: (x) => x.channel },
              { key: "amt", header: "Amount", className: "text-right", cell: (x) => <span className={`tabular font-mono text-[12.5px] ${x.type === "credit" ? "text-success" : ""}`}>{x.type === "credit" ? "+" : "−"}{formatINR(x.amount)}</span> },
              { key: "st", header: "Status", cell: (x) => <StatusDot tone="success" label={x.status} /> },
            ]} />
        </Section>
        <Section title="Cases & service requests">
          <Panel className="divide-y divide-border">
            {[...c.cases.map((x) => ({ ...x, kind: "Case" })), ...c.serviceRequests.map((x) => ({ ...x, kind: "Request" }))].map((x) => (
              <div key={x.id} className="p-4"><p className="text-small">{x.title}</p><p className="text-meta text-muted">{x.kind} {x.id} · {x.status} · opened {formatDate(x.opened)}</p></div>
            ))}
            {c.cases.length + c.serviceRequests.length === 0 && <p className="p-4 text-small text-muted">No cases or requests.</p>}
          </Panel>
        </Section>
      </div>
    </PageContainer>
  );
}
