"use client";
import { AudioLines, Headset, PanelRight, RotateCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef } from "react";
import { HandoffBanner } from "@/components/handoff/handoff-banner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { AuthBadge } from "@/components/ui/domain-badges";
import { StatusDot } from "@/components/ui/status-dot";
import { useConversation } from "@/hooks/use-conversation";
import { sessionUsable, useCustomerSession } from "@/store/customer-session";
import { useUi } from "@/store/ui";
import { languageLabel } from "@/utils/format";
import { cn } from "@/utils/cn";
import { Composer } from "./composer";
import { ContextPanel } from "./context-panel";
import { MessageView } from "./message";
import { SessionGate } from "./session-gate";

const SUGGESTIONS = ["What are home loan foreclosure charges?", "What is my loan balance?", "Mera credit card ka outstanding kitna hai?", "Block my card", "Transfer ₹1,00,000 to Rahul"];

export function ChatWorkspace({ audience, voiceHref }: { audience: "customer" | "operator"; voiceHref: string }) {
  const session = useCustomerSession();
  if (!session.sessionId || !sessionUsable(session)) return <SessionGate audience={audience} />;
  return <ChatInner audience={audience} voiceHref={voiceHref} />;
}

function ChatInner({ audience, voiceHref }: { audience: "customer" | "operator"; voiceHref: string }) {
  const router = useRouter();
  const { state, socketStatus, send, sendOtp, confirm, cancel, interrupt } = useConversation();
  const clear = useCustomerSession((s) => s.clear);
  const { contextOpen, setContextOpen } = useUi();
  const endRef = useRef<HTMLDivElement>(null);
  const busy = state.phase !== "idle";

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [state.messages]);

  const lastCustomer = useMemo(() => [...state.messages].reverse().find((m) => m.role === "customer" && !m.masked)?.text, [state.messages]);
  const slipActions = {
    confirm, cancel, otp: sendOtp,
    identify: (phone: string) => send(`My registered mobile number is ${phone}`, { display: `Mobile number ending ${phone.slice(-4)}` }),
    checkApproval: () => send("Is my request approved?"),
    retry: () => lastCustomer && send(lastCustomer),
    support: () => send("I want to talk to a human agent"),
  };
  const online = socketStatus === "open";
  const pendingOtp = state.pendingAction?.decision === "REQUIRE_AUTH";

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="grid-lines flex h-12 shrink-0 items-center gap-3 border-b border-border px-4">
          <div className="min-w-0">
            <p className="truncate text-small font-medium text-strong">{audience === "operator" ? "Customer support agent" : "Demo Bank assistant"}</p>
          </div>
          <StatusDot tone={online ? "success" : socketStatus === "reconnecting" || socketStatus === "connecting" ? "warning" : "neutral"}
            pulse={socketStatus === "reconnecting"} className="text-meta text-muted"
            label={online ? "Online" : socketStatus === "reconnecting" ? "Reconnecting" : socketStatus === "connecting" ? "Connecting" : "Offline · using fallback"} />
          <div className="ml-auto flex items-center gap-2">
            {state.language !== "en" && <Badge tone="neutral" aria-label={`Detected language ${languageLabel(state.language, state.languageTag)}`}>Detected: {languageLabel(state.language, state.languageTag)}</Badge>}
            <span className="hidden sm:inline"><AuthBadge state={state.authState} /></span>
            <Button size="sm" variant="ghost" onClick={() => router.push(voiceHref)}><AudioLines />Voice</Button>
            <Button size="sm" variant="ghost" className="hidden sm:inline-flex" disabled={state.handoff.status !== "none"} onClick={() => send("I want to talk to a human agent")}><Headset />Talk to a person</Button>
            {audience === "operator" && <Button size="icon-sm" variant="ghost" className="xl:hidden" aria-label="Customer context" onClick={() => setContextOpen(true)}><PanelRight /></Button>}
            <Button size="icon-sm" variant="ghost" aria-label="End and start a new conversation" onClick={() => clear()}><RotateCcw /></Button>
          </div>
        </div>
        <HandoffBanner handoff={state.handoff} />
        <div className="min-h-0 flex-1 overflow-y-auto" aria-live="polite" aria-relevant="additions">
          <div className="mx-auto grid w-full max-w-3xl gap-6 px-4 py-6">
            {state.messages.length === 0 && (
              <div className="grid gap-4 py-8">
                <p className="text-section font-medium text-strong">How can I help today?</p>
                <p className="text-small text-muted">Ask in English, हिन्दी, Hinglish or another Indian language — no need to choose first.</p>
                <div className="flex flex-wrap gap-2">
                  {SUGGESTIONS.map((s) => <button key={s} onClick={() => send(s)} className="rounded-full border border-border-strong px-3 py-1.5 text-small text-muted hover:border-[#3a3a3a] hover:text-foreground">{s}</button>)}
                </div>
              </div>
            )}
            {state.messages.map((m, i) => (
              <MessageView key={m.id} m={m} audience={audience} slipActions={slipActions} busy={busy}
                onRegenerate={i === state.messages.length - 1 && lastCustomer ? () => send(lastCustomer) : undefined} />
            ))}
            <div ref={endRef} />
          </div>
        </div>
        <Composer onSend={(t) => send(t)} busy={busy} onStop={interrupt} onVoice={() => router.push(voiceHref)}
          hint={pendingOtp ? "Enter the one-time password in the verification card above — it won't appear in the conversation." : state.handoff.status === "requested" || state.handoff.status === "connected" ? "Messages now go to the specialist." : undefined} />
      </div>
      {audience === "operator" && (
        <>
          <aside aria-label="Customer context" className={cn("hidden w-80 shrink-0 overflow-y-auto border-l border-border bg-background xl:block")}>
            <ContextPanel state={state} />
          </aside>
          <Dialog open={contextOpen} onOpenChange={setContextOpen}>
            <DialogContent title="Customer context" side="bottom" className="xl:hidden"><div className="-mx-5 -my-4"><ContextPanel state={state} /></div></DialogContent>
          </Dialog>
        </>
      )}
    </div>
  );
}
