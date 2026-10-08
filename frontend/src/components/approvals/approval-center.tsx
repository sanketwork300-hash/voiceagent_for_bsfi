"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useState } from "react";
import { PageContainer } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { RiskBadge } from "@/components/ui/domain-badges";
import { KeyValue, PageHeader } from "@/components/ui/page";
import { EmptyState, ErrorState, SkeletonRows } from "@/components/ui/states";
import { useTenantKey } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ApiError } from "@/lib/api/errors";
import type { ApprovalRequest } from "@/types/domain";
import { formatINR, formatRelative, titleCase } from "@/utils/format";

function ApprovalRow({ a }: { a: ApprovalRequest }) {
  const t = useTenantKey();
  const qc = useQueryClient();
  const [decision, setDecision] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const m = useMutation({
    mutationFn: (approve: boolean) => apiClient.approvals.decide(a.id, approve),
    onSuccess: () => qc.invalidateQueries({ queryKey: [t, "approvals"] }),
    onError: (e) => setError(e instanceof ApiError ? e.userMessage : "The decision wasn't saved."),
  });
  const amount = typeof a.arguments.amount === "number" ? formatINR(a.arguments.amount) : null;
  return (
    <li aria-label={`Approval for session ${a.session_id.slice(-8)}`} className="grid gap-4 rounded-panel border border-border bg-surface p-5 md:grid-cols-[1fr_auto]">
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2"><p className="text-lead font-medium text-strong">{titleCase(a.tool)}</p><RiskBadge level={a.risk_level} /></div>
        {amount && <p className="tabular font-mono text-[26px] leading-none text-strong">{amount}</p>}
        <KeyValue items={[
          { label: "Payee", value: String(a.arguments.payee_name ?? "—") },
          { label: "Session", value: a.session_id.slice(-8), mono: true },
          { label: "Reason", value: a.reason },
          { label: "Requested", value: formatRelative(a.created_at) },
        ]} />
        <p className="text-meta text-muted">The customer has already passed transaction OTP. After approval they must still confirm before money moves.</p>
        {error && <p role="alert" className="text-small text-danger">{error}</p>}
      </div>
      <div className="flex items-start gap-2 md:flex-col md:items-stretch">
        <Button variant="success" onClick={() => setDecision("approve")}><Check />Approve</Button>
        <Button variant="danger-outline" onClick={() => setDecision("deny")}><X />Deny</Button>
      </div>
      <ConfirmDialog open={decision !== null} onOpenChange={(o) => !o && setDecision(null)} tone={decision === "deny" ? "danger" : "primary"}
        title={decision === "approve" ? `Approve ${amount ?? "this request"}?` : "Deny this request?"}
        description={decision === "approve" ? "The customer will be asked to confirm, then the transfer is sent to the bank." : "The customer is told the request wasn't approved. Nothing is sent to the bank."}
        consequence="Your decision is recorded in the audit log."
        confirmLabel={decision === "approve" ? "Approve transfer" : "Deny request"} onConfirm={() => m.mutateAsync(decision === "approve")} />
    </li>
  );
}

export function ApprovalCenter() {
  const t = useTenantKey();
  const q = useQuery({ queryKey: [t, "approvals"], queryFn: apiClient.approvals.list, refetchInterval: 5000 });
  return (
    <PageContainer>
      <PageHeader title="Approvals" description="Maker-checker queue for actions above policy thresholds." />
      {q.error ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <SkeletonRows /> : q.data.length === 0 ? (
        <EmptyState title="Nothing to approve" description="Transfers above the approval threshold wait here until a supervisor decides." />
      ) : <ul className="grid gap-3">{q.data.map((a) => <ApprovalRow key={a.id} a={a} />)}</ul>}
    </PageContainer>
  );
}
