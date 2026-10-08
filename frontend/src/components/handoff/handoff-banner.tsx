import { Check, Headset } from "lucide-react";
import type { ConversationState } from "@/lib/chat/conversation";
import { titleCase } from "@/utils/format";

const REASON: Record<string, string> = {
  FRAUD: "Fraud investigation", CUSTOMER_REQUEST: "You asked for a person", AUTHENTICATION_FAILURE: "Identity verification",
  POLICY_REJECTION: "Request needs a specialist", REPEATED_FAILURE: "We couldn't complete this automatically",
  HIGH_RISK_OPERATION: "High-value request", COMPLEX_COMPLAINT: "Complaint",
};

/** Shown in both chat and voice: the customer should never need to repeat themselves. */
export function HandoffBanner({ handoff }: { handoff: ConversationState["handoff"] }) {
  if (handoff.status === "none") return null;
  const connected = handoff.status === "connected";
  const resolved = handoff.status === "resolved";
  return (
    <div role="status" className="mx-3 mt-3 rounded-[8px] border border-info/30 bg-info/[0.06] px-4 py-3 sm:mx-4">
      <p className="flex items-center gap-2 text-small font-medium text-foreground">
        <Headset aria-hidden className="size-4 text-info" />
        {resolved ? "The specialist has finished — you're back with the assistant." : connected ? "A specialist has joined the conversation." : "Connecting you with a specialist…"}
      </p>
      {!resolved && <>
        {handoff.reason && <p className="mt-1 text-small text-muted">Reason: {REASON[handoff.reason] ?? titleCase(handoff.reason)}{handoff.priority === "urgent" ? " · priority" : ""}</p>}
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted" aria-label="Context shared with the specialist">
          {["Conversation", "Verification status", "Recent activity", "Actions already taken", "Summary"].map((c) => (
            <li key={c} className="inline-flex items-center gap-1"><Check aria-hidden className="size-3 text-success" />{c}</li>
          ))}
        </ul>
      </>}
    </div>
  );
}
