"use client";
import { useQuery } from "@tanstack/react-query";
import { Lock, ShieldCheck, ShieldX } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { KeyValue, PageHeader } from "@/components/ui/page";
import { Select } from "@/components/ui/select";
import { useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import type { AuditEvent } from "@/types/domain";
import { formatDate, formatTime, maskId, titleCase } from "@/utils/format";

const OUTCOME_TONE = (o: string) => (/success|ALLOW/i.test(o) ? "success" : /fail|DENY|blocked/i.test(o) ? "danger" : /REQUIRE|flagged/i.test(o) ? "warning" : "neutral");

export function describeAudit(e: AuditEvent): string {
  if (e.event_type === "tool.decision") return `${e.resource} held by policy (${titleCase(e.outcome)})`;
  if (e.event_type === "tool.executed") return `${e.resource} ${e.outcome === "success" ? "executed" : "failed"}`;
  return titleCase(e.event_type.replace(/\./g, " "));
}

export function AuditLog({ preset }: { preset?: { prefix: string; title: string; description: string } }) {
  const t = useTenantKey();
  const params = useSearchParams();
  const [type, setType] = useState("all");
  const [outcome, setOutcome] = useState(params.get("outcome") ?? "all");
  const [channel, setChannel] = useState("all");
  const [text, setText] = useState(params.get("q") ?? "");
  const [from, setFrom] = useState("");
  const [open, setOpen] = useState<AuditEvent | null>(null);
  const q = useQuery({ queryKey: [t, "audit", "all"], queryFn: () => apiClient.audit.events({ limit: 1000 }), refetchInterval: 15_000 });
  const verify = useQuery({ queryKey: [t, "audit", "verify"], queryFn: apiClient.audit.verify, enabled: false });
  const types = useMemo(() => Array.from(new Set((q.data ?? []).map((e) => e.event_type))).sort(), [q.data]);
  const rows = useMemo(() => (q.data ?? []).filter((e) =>
    (!preset || e.event_type.startsWith(preset.prefix)) && (type === "all" || e.event_type === type)
    && (outcome === "all" || (outcome === "failure" ? /fail|DENY|blocked/i.test(e.outcome) : e.outcome === outcome))
    && (channel === "all" || e.channel === channel) && (!from || e.occurred_at >= from)
    && (!text || `${e.resource ?? ""} ${e.actor_id ?? ""} ${e.session_id ?? ""} ${JSON.stringify(e.payload)}`.toLowerCase().includes(text.toLowerCase()))),
  [q.data, type, outcome, channel, from, text, preset]);
  const columns: Column<AuditEvent>[] = [
    { key: "seq", header: "#", cell: (e) => <span className="tabular font-mono text-[12px] text-muted">{e.seq}</span>, sort: (e) => e.seq },
    { key: "time", header: "Time", cell: (e) => <span className="tabular font-mono text-[12px]">{formatDate(e.occurred_at, { day: "2-digit", month: "short" })} {formatTime(e.occurred_at)}</span>, sort: (e) => e.occurred_at },
    { key: "event", header: "Event", primary: true, cell: (e) => <span><span className="block font-mono text-[12.5px] text-foreground">{e.event_type}</span><span className="text-meta text-muted">{describeAudit(e)}</span></span> },
    { key: "actor", header: "Actor", cell: (e) => <span className="text-muted">{titleCase(e.actor_type)}{e.actor_id ? ` ${maskId(e.actor_id)}` : ""}</span> },
    { key: "channel", header: "Channel", cell: (e) => e.channel ?? "—", hideOnMobile: true },
    { key: "outcome", header: "Outcome", cell: (e) => <Badge tone={OUTCOME_TONE(e.outcome)}>{titleCase(e.outcome)}</Badge> },
    { key: "hash", header: "Hash", hideOnMobile: true, cell: (e) => <span className="font-mono text-[11.5px] text-subtle">{e.hash.slice(0, 10)}</span> },
  ];
  return (
    <PageContainer wide>
      <PageHeader title={preset?.title ?? "Audit logs"} description={preset?.description ?? "Append-only, hash-chained record of every decision and action. Payloads are PII-redacted."}
        actions={<Button onClick={() => verify.refetch()} loading={verify.isFetching}><Lock />Verify chain integrity</Button>}
        meta={verify.data && (verify.data.intact
          ? <span className="inline-flex items-center gap-1.5 text-success"><ShieldCheck aria-hidden className="size-3.5" />Chain intact — no events altered or removed</span>
          : <span className="inline-flex items-center gap-1.5 text-danger"><ShieldX aria-hidden className="size-3.5" />Chain broken at event #{verify.data.first_broken_seq}</span>)} />
      <div className="mb-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
        {!preset && <Select aria-label="Event type" value={type} onValueChange={setType} options={[{ value: "all", label: "All events" }, ...types.map((x) => ({ value: x, label: x }))]} />}
        <Select aria-label="Outcome" value={outcome} onValueChange={setOutcome} options={[{ value: "all", label: "Any outcome" }, { value: "success", label: "Success" }, { value: "failure", label: "Failed or blocked" }, { value: "REQUIRE_AUTH", label: "Required authentication" }, { value: "REQUIRE_CONFIRMATION", label: "Required confirmation" }]} />
        <Select aria-label="Channel" value={channel} onValueChange={setChannel} options={[{ value: "all", label: "Any channel" }, { value: "chat", label: "Chat" }, { value: "voice", label: "Voice" }]} />
        <Input aria-label="From date" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
        <Input aria-label="Search tool, user, customer or session" placeholder="Tool, user, customer, session…" value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      <DataTable caption="Audit events" rows={q.data ? rows : undefined} error={q.error} onRetry={() => q.refetch()} columns={columns} getKey={(e) => String(e.seq)} onRowClick={setOpen} pageSize={50}
        empty={{ title: "No matching events", description: "Adjust the filters. Every tool decision, verification, approval and handoff is recorded here." }} />
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        {open && <DialogContent title={`Event #${open.seq}`} description={describeAudit(open)} side="right">
          <KeyValue items={[
            { label: "Type", value: open.event_type, mono: true }, { label: "Time", value: `${formatDate(open.occurred_at)} ${formatTime(open.occurred_at)}` },
            { label: "Actor", value: `${open.actor_type}${open.actor_id ? ` · ${maskId(open.actor_id)}` : ""}` }, { label: "Resource", value: open.resource ?? "—", mono: true },
            { label: "Session", value: open.session_id ? maskId(open.session_id, 8) : "—", mono: true }, { label: "Outcome", value: open.outcome },
            { label: "Trace", value: open.trace_id ?? "—", mono: true }, { label: "Hash", value: open.hash, mono: true },
          ]} />
          <p className="mb-1.5 mt-5 text-small font-medium text-strong">Payload (redacted)</p>
          <pre className="overflow-x-auto rounded-control border border-border bg-background p-3 font-mono text-[12px] text-muted">{JSON.stringify(open.payload, null, 2)}</pre>
        </DialogContent>}
      </Dialog>
    </PageContainer>
  );
}
