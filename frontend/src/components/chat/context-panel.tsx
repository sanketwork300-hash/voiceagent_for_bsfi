"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { AuthLadder } from "@/components/authentication/auth-ladder";
import { Badge } from "@/components/ui/badge";
import { KeyValue } from "@/components/ui/page";
import { StatusDot, healthTone } from "@/components/ui/status-dot";
import { apiClient } from "@/lib/api/client";
import type { ConversationState } from "@/lib/chat/conversation";
import { useCustomerSession } from "@/store/customer-session";
import { formatINR, formatMs, languageLabel, maskId, titleCase } from "@/utils/format";

type Accounts = { accounts?: { account_type: string; account_number_masked: string; available_balance: number }[] };
type Cards = { cards?: { card_type: string; card_number_masked: string; status: string; outstanding_amount?: number }[] };

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="grid gap-2 border-b border-border px-4 py-4 last:border-0"><h3 className="text-small font-medium text-strong">{title}</h3>{children}</section>;
}

/** Operator-only context rail: what the AI knows and did. Values come from backend responses (already masked). */
export function ContextPanel({ state }: { state: ConversationState }) {
  const { session, customerId, kind } = useCustomerSession();
  const customer = useQuery({ queryKey: ["customer", customerId], queryFn: () => apiClient.customers.get(customerId!), enabled: Boolean(customerId) });
  const steps = state.messages.flatMap((m) => m.tools.map((t) => ({ ...t, at: m.at })));
  const latest = <T,>(tool: string) => [...steps].reverse().find((s) => s.tool === tool && s.status === "completed")?.result as T | undefined;
  const acc = latest<Accounts>("get_account_balance");
  const cards = latest<Cards>("get_card_status");
  const holds = state.messages.flatMap((m) => m.slips).filter((s) => s.kind === "confirm" || s.kind === "verify" || s.kind === "approval");
  const sources = state.messages.flatMap((m) => m.sources);
  const failedOtp = state.messages.some((m) => /didn.t match/i.test(m.text));
  return (
    <div className="text-small">
      <Block title="Customer">
        {customer.data ? (
          <div className="grid gap-1">
            <Link href={`/customers/${customer.data.id}`} className="text-body font-medium text-foreground hover:underline">{customer.data.name}</Link>
            <p className="text-meta text-muted">{customer.data.phoneMasked} · customer since {customer.data.customerSince}</p>
          </div>
        ) : <p className="text-muted">{kind === "anonymous" ? "Not signed in. Identity is established inside the conversation." : "Identity held by the bank."}</p>}
      </Block>
      <Block title="Authentication"><AuthLadder state={state.authState} failed={failedOtp} compact /></Block>
      <Block title="Session">
        <KeyValue items={[
          { label: "Session", value: session?.session_id ? maskId(session.session_id, 8) : "—", mono: true },
          { label: "Channels", value: session?.active_channels?.join(" → ") ?? "chat" },
          { label: "Language", value: languageLabel(state.language, state.languageTag) },
          { label: "Intent", value: state.intent ? titleCase(state.intent) : "—" },
          { label: "Pending", value: state.pendingAction ? <Badge tone="warning">{titleCase(state.pendingAction.decision)}</Badge> : "None" },
        ]} />
      </Block>
      {(acc?.accounts?.length || cards?.cards?.length) ? (
        <Block title="Accounts seen in this conversation">
          <ul className="grid gap-1.5">
            {acc?.accounts?.map((a) => <li key={a.account_number_masked} className="flex justify-between gap-3"><span>{titleCase(a.account_type)} {maskId(a.account_number_masked)}</span><span className="tabular font-mono text-[12.5px]">{formatINR(a.available_balance)}</span></li>)}
            {cards?.cards?.map((c) => <li key={c.card_number_masked} className="flex justify-between gap-3"><span>{titleCase(c.card_type)} card {maskId(c.card_number_masked)}</span><StatusDot tone={healthTone(c.status)} label={titleCase(c.status)} /></li>)}
          </ul>
        </Block>
      ) : null}
      <Block title="Activity">
        {steps.length === 0 ? <p className="text-muted">Tool calls will appear here as the agent works.</p> : (
          <ul className="grid gap-1.5">
            {steps.slice(-8).reverse().map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-2">
                <span className="flex min-w-0 items-center gap-2"><StatusDot tone={s.status === "completed" ? "success" : s.status === "running" ? "neutral" : "danger"} pulse={s.status === "running"} />
                  <span className="truncate font-mono text-[12px]">{s.tool}</span></span>
                <span className="shrink-0 font-mono text-meta text-muted">{s.status === "running" ? "…" : formatMs(s.latencyMs)}</span>
              </li>
            ))}
          </ul>
        )}
      </Block>
      {holds.length > 0 && (
        <Block title="Policy decisions">
          <ul className="grid gap-1.5">
            {holds.map((h) => (
              <li key={h.id} className="flex items-center justify-between gap-2">
                <span className="truncate">{h.kind === "verify" ? "Require authentication" : h.kind === "confirm" ? "Require confirmation" : "Require human approval"}</span>
                <span className="text-meta text-muted">{"state" in h ? titleCase(h.state) : ""}</span>
              </li>
            ))}
          </ul>
        </Block>
      )}
      {sources.length > 0 && <Block title="Knowledge used"><p className="text-muted">{sources.length} passage{sources.length > 1 ? "s" : ""} from {new Set(sources.map((s) => s.document_id)).size} document(s)</p></Block>}
    </div>
  );
}
