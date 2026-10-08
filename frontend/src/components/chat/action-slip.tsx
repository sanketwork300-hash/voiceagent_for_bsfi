"use client";
import { BadgeCheck, Ban, CircleAlert, Clock3, Copy, KeyRound, ShieldCheck, TriangleAlert } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { ACTION_TOOLS } from "@/config/tools";
import type { Slip } from "@/lib/chat/conversation";
import { AUTH_LABEL } from "@/components/ui/domain-badges";
import { formatINR, maskId, titleCase } from "@/utils/format";
import { cn } from "@/utils/cn";

type Tone = "neutral" | "warning" | "success" | "danger" | "info";
const STRIPE: Record<Tone, string> = {
  neutral: "before:bg-border-strong", warning: "before:bg-warning", success: "before:bg-success", danger: "before:bg-danger", info: "before:bg-info",
};

/** The ledger slip: a financial/account action is a distinct object, never just another chat bubble. */
function SlipFrame({ tone, kicker, icon, children, label }: { tone: Tone; kicker: string; icon: React.ReactNode; children: React.ReactNode; label: string }) {
  return (
    <section aria-label={label}
      className={cn("relative mt-3 max-w-[420px] overflow-hidden rounded-[8px] border border-border-strong bg-surface pl-[3px] before:absolute before:inset-y-0 before:left-0 before:w-[3px]", STRIPE[tone])}>
      <div className="flex items-center gap-2 border-b border-border px-4 py-2 text-meta text-muted [&_svg]:size-3.5">{icon}{kicker}</div>
      <div className="px-4 py-3.5">{children}</div>
    </section>
  );
}

function Rows({ rows }: { rows: [string, React.ReactNode][] }) {
  const visible = rows.filter(([, v]) => v !== undefined && v !== null && v !== "");
  if (!visible.length) return null;
  return (
    <dl className="mt-3 grid grid-cols-[96px_1fr] gap-x-4 gap-y-1.5 text-small">
      {visible.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="min-w-0 break-words text-foreground">{v}</dd></div>)}
    </dl>
  );
}

function Amount({ value }: { value: unknown }) {
  if (typeof value !== "number") return null;
  return <p className="tabular font-mono text-[26px] font-medium leading-none tracking-[-0.02em] text-strong">{formatINR(value)}</p>;
}

function detailRows(details: Record<string, unknown> | undefined): [string, React.ReactNode][] {
  if (!details) return [];
  const d = details;
  return [
    ["To", d.payee_name as string],
    ["Card", d.card_type ? `${titleCase(String(d.card_type))} card` : undefined],
    ["Reason", d.reason ? titleCase(String(d.reason)) : undefined],
    ["Remarks", d.remarks as string],
  ];
}

function useCountdown(iso?: string) {
  const [left, setLeft] = useState(() => (iso ? new Date(iso).getTime() - Date.now() : 0));
  useEffect(() => {
    if (!iso) return;
    const t = setInterval(() => setLeft(new Date(iso).getTime() - Date.now()), 1000);
    return () => clearInterval(t);
  }, [iso]);
  const s = Math.max(0, Math.floor(left / 1000));
  return { expired: Boolean(iso) && left <= 0, label: `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` };
}

function ConfirmSlip({ slip, onConfirm, onCancel, busy }: { slip: Extract<Slip, { kind: "confirm" }>; onConfirm: () => void; onCancel: () => void; busy: boolean }) {
  const meta = ACTION_TOOLS[slip.tool];
  const { expired, label } = useCountdown(slip.state === "open" ? slip.expiresAt : undefined);
  const open = slip.state === "open" && !expired;
  return (
    <SlipFrame tone={open ? "warning" : slip.state === "confirmed" ? "success" : "neutral"} label={`${meta?.title ?? "Action"} confirmation`}
      kicker={open ? "Financial action · needs your confirmation" : "Financial action"} icon={<ShieldCheck aria-hidden />}>
      <p className="text-small font-medium text-foreground">{meta?.title ?? titleCase(slip.tool)}</p>
      <div className="mt-2"><Amount value={slip.details?.amount} /></div>
      <Rows rows={detailRows(slip.details)} />
      <p className="mt-3 text-small text-muted">{slip.summary.charAt(0).toUpperCase() + slip.summary.slice(1)}.</p>
      {open && meta?.consequence && <p className="mt-1 text-small text-foreground">{meta.consequence}</p>}
      <div className="tear mt-4" />
      {open ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
          <Button variant={slip.tool === "block_card" ? "danger" : "primary"} onClick={onConfirm} loading={busy}>{meta?.confirmLabel ?? "Confirm"}</Button>
          <span className="ml-auto inline-flex items-center gap-1 text-meta text-muted"><Clock3 aria-hidden className="size-3" />Expires in {label}</span>
        </div>
      ) : (
        <p className="mt-3 text-small text-muted">
          {slip.state === "confirmed" ? "Confirmed. See the result below." : expired && slip.state === "open"
            ? "This request expired for your security. Nothing was changed." : slip.state === "superseded"
            ? "Replaced by a newer request." : "Cancelled. Nothing was changed."}
        </p>
      )}
    </SlipFrame>
  );
}

function VerifySlip({ slip, onOtp, onIdentify, busy }: { slip: Extract<Slip, { kind: "verify" }>; onOtp: (c: string) => void; onIdentify: (p: string) => void; busy: boolean }) {
  const [value, setValue] = useState("");
  const id = useId();
  const open = slip.state === "open";
  const txn = slip.purpose === "transaction";
  return (
    <SlipFrame tone={open ? "info" : slip.state === "done" ? "success" : "neutral"} label="Additional verification" icon={<KeyRound aria-hidden />}
      kicker={open ? "Additional verification required" : slip.state === "done" ? "Verified" : "Verification closed"}>
      <Rows rows={[
        ["Level", slip.requiredAuth ? (txn ? "Transaction authentication" : AUTH_LABEL[slip.requiredAuth]) : undefined],
        ["For", slip.summary ? slip.summary : undefined],
        ["Code sent to", slip.destination ? maskId(slip.destination) : undefined],
      ]} />
      {open && slip.step === "otp" && (
        <form className="mt-4 grid gap-2" onSubmit={(e) => { e.preventDefault(); if (/^\d{4,8}$/.test(value)) { onOtp(value); setValue(""); } }}>
          <label htmlFor={id} className="text-small text-foreground">One-time password</label>
          <div className="flex gap-2">
            <input id={id} type="password" inputMode="numeric" autoComplete="one-time-code" maxLength={8} value={value}
              onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))} aria-describedby={`${id}-hint`}
              className="h-9 w-40 rounded-control border border-border-strong bg-background px-3 font-mono text-lead tracking-[0.35em] text-strong focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-strong/60" />
            <Button type="submit" variant="primary" disabled={!/^\d{4,8}$/.test(value)} loading={busy}>Verify</Button>
          </div>
          <p id={`${id}-hint`} className="text-meta text-muted">Never share this code with anyone, including bank staff. It isn&apos;t stored in this conversation.</p>
        </form>
      )}
      {open && slip.step === "identify" && (
        <form className="mt-4 grid gap-2" onSubmit={(e) => { e.preventDefault(); if (/^[6-9]\d{9}$/.test(value)) { onIdentify(value); setValue(""); } }}>
          <label htmlFor={id} className="text-small text-foreground">Registered mobile number</label>
          <div className="flex gap-2">
            <input id={id} inputMode="tel" autoComplete="tel-national" maxLength={10} value={value} onChange={(e) => setValue(e.target.value.replace(/\D/g, ""))}
              className="h-9 w-44 rounded-control border border-border-strong bg-background px-3 font-mono text-body text-strong focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-strong/60" />
            <Button type="submit" variant="primary" disabled={!/^[6-9]\d{9}$/.test(value)} loading={busy}>Send code</Button>
          </div>
          <p className="text-meta text-muted">We&apos;ll send a one-time password to this number if it&apos;s registered with the bank.</p>
        </form>
      )}
    </SlipFrame>
  );
}

function ApprovalSlip({ slip, onCheck }: { slip: Extract<Slip, { kind: "approval" }>; onCheck: () => void }) {
  const meta = ACTION_TOOLS[slip.tool];
  return (
    <SlipFrame tone={slip.state === "pending" ? "info" : "neutral"} label="Bank approval" icon={<Clock3 aria-hidden />}
      kicker={slip.state === "pending" ? "Waiting for bank approval" : "Approval step closed"}>
      <p className="text-small font-medium text-foreground">{meta?.title ?? titleCase(slip.tool)}</p>
      <div className="mt-2"><Amount value={slip.details?.amount} /></div>
      <Rows rows={[...detailRows(slip.details), ["Why", slip.reason], ["Reference", slip.approvalId ? <span className="font-mono text-[12.5px]">{slip.approvalId.slice(0, 8).toUpperCase()}</span> : undefined]]} />
      {slip.state === "pending" && <>
        <p className="mt-3 text-small text-muted">Nothing has been sent yet. Once a bank officer approves, you&apos;ll be asked to confirm.</p>
        <div className="tear mt-4" />
        <div className="mt-3"><Button size="sm" onClick={onCheck}>Check approval status</Button></div>
      </>}
    </SlipFrame>
  );
}

function ReceiptSlip({ slip }: { slip: Extract<Slip, { kind: "receipt" }> }) {
  const meta = ACTION_TOOLS[slip.tool];
  const r = slip.result;
  const ref = (r.transaction_ref ?? r.reference) as string | undefined;
  const [copied, setCopied] = useState(false);
  return (
    <SlipFrame tone="success" label={meta?.successTitle ?? "Completed"} kicker="Confirmed by the bank" icon={<BadgeCheck aria-hidden />}>
      <p className="text-small font-medium text-success">{meta?.successTitle ?? "Completed"}</p>
      <div className="mt-2"><Amount value={r.amount} /></div>
      <Rows rows={[
        ["To", r.payee_name ? `${r.payee_name}${r.payee_account_masked ? ` · ${maskId(String(r.payee_account_masked))}` : ""}` : undefined],
        ["Card", r.card_number_masked ? `${titleCase(String(r.card_type ?? ""))} card ${maskId(String(r.card_number_masked))}` : undefined],
        ["Status", r.status ? titleCase(String(r.status)) : undefined],
        [slip.tool === "transfer_money" ? "Transaction ID" : "Reference", ref ? <span className="font-mono text-[12.5px] text-strong">{ref}</span> : undefined],
      ]} />
      {ref && <><div className="tear mt-4" />
        <div className="mt-3">
          <Button size="sm" variant="ghost" onClick={async () => { await navigator.clipboard.writeText(ref); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
            <Copy />{copied ? "Copied" : "Copy reference"}
          </Button>
        </div></>}
    </SlipFrame>
  );
}

const FAILURE_COPY = {
  not_executed: { status: "Not processed", body: "This request never reached the bank, so no money moved and nothing changed.", retry: true },
  rejected: { status: "Not processed", body: "The bank declined this request. No amount was debited and nothing changed.", retry: true },
  unknown: { status: "Not confirmed", body: "The bank didn't confirm this request. Check your recent transactions before trying again — we won't guess whether it went through.", retry: false },
} as const;

function FailureSlip({ slip, onRetry, onSupport }: { slip: Extract<Slip, { kind: "failure" }>; onRetry: () => void; onSupport: () => void }) {
  const meta = ACTION_TOOLS[slip.tool];
  const copy = FAILURE_COPY[slip.outcome];
  const blocked = slip.policyDecision === "DENY";
  return (
    <SlipFrame tone={slip.outcome === "unknown" ? "warning" : "danger"} label={meta?.failureTitle ?? "Action failed"}
      kicker={slip.outcome === "unknown" ? "Outcome unconfirmed" : "Action not completed"} icon={slip.outcome === "unknown" ? <TriangleAlert aria-hidden /> : <Ban aria-hidden />}>
      <p className={cn("text-small font-medium", slip.outcome === "unknown" ? "text-warning" : "text-danger")}>{meta?.failureTitle ?? "Action not completed"}</p>
      <Rows rows={[["Status", copy.status], ["Reason", blocked ? "Not allowed by the bank's policy" : slip.error ?? undefined]]} />
      <p className="mt-3 text-small text-foreground">{copy.body}</p>
      <div className="tear mt-4" />
      <div className="mt-3 flex gap-2">
        {copy.retry && !blocked && <Button size="sm" onClick={onRetry}>Try again</Button>}
        <Button size="sm" variant="ghost" onClick={onSupport}><CircleAlert />Contact support</Button>
      </div>
    </SlipFrame>
  );
}

export function ActionSlip({ slip, actions, busy }: {
  slip: Slip; busy?: boolean;
  actions: { confirm: () => void; cancel: () => void; otp: (c: string) => void; identify: (p: string) => void; checkApproval: () => void; retry: () => void; support: () => void };
}) {
  switch (slip.kind) {
    case "confirm": return <ConfirmSlip slip={slip} onConfirm={actions.confirm} onCancel={actions.cancel} busy={!!busy} />;
    case "verify": return <VerifySlip slip={slip} onOtp={actions.otp} onIdentify={actions.identify} busy={!!busy} />;
    case "approval": return <ApprovalSlip slip={slip} onCheck={actions.checkApproval} />;
    case "receipt": return <ReceiptSlip slip={slip} />;
    case "failure": return <FailureSlip slip={slip} onRetry={actions.retry} onSupport={actions.support} />;
  }
}
