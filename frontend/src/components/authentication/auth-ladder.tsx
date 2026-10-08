import { Check, Circle, Dot } from "lucide-react";
import type { AuthState } from "@/types/domain";
import { AUTH_ORDER } from "@/types/domain";
import { cn } from "@/utils/cn";

const STEPS: { state: AuthState; label: string; detail: string }[] = [
  { state: "IDENTIFIED", label: "Identity identified", detail: "Customer reference known (app login, caller ID or mobile number)" },
  { state: "PARTIALLY_AUTHENTICATED", label: "Partially verified", detail: "A weak factor such as voice match or security questions" },
  { state: "FULLY_AUTHENTICATED", label: "Session authenticated", detail: "Bank login or one-time password" },
  { state: "TRANSACTION_AUTHENTICATED", label: "Transaction authenticated", detail: "One-time password bound to a single transaction" },
];

/** Authentication timeline. Status comes only from the backend session; the UI never infers verification. */
export function AuthLadder({ state, failed, compact }: { state: AuthState; failed?: boolean; compact?: boolean }) {
  const level = AUTH_ORDER.indexOf(state);
  return (
    <ol className="grid gap-0" aria-label="Authentication status">
      {STEPS.map((s, i) => {
        const stepLevel = AUTH_ORDER.indexOf(s.state);
        const reached = level >= stepLevel;
        const next = !reached && stepLevel === Math.max(level + 1, 1);
        const status = reached ? "Verified" : next && failed ? "Failed" : next ? "Pending" : "Not started";
        return (
          <li key={s.state} className="relative grid grid-cols-[18px_1fr] gap-x-2.5 pb-3 last:pb-0">
            {i < STEPS.length - 1 && <span aria-hidden className={cn("absolute left-[8.5px] top-[18px] h-[calc(100%-14px)] w-px", reached ? "bg-success/50" : "bg-border-strong")} />}
            <span aria-hidden className={cn("grid size-[18px] place-items-center rounded-full border",
              reached ? "border-success/60 bg-success/15 text-success" : next ? (failed ? "border-danger/60 text-danger" : "border-warning/60 text-warning") : "border-border-strong text-subtle")}>
              {reached ? <Check className="size-3" /> : next ? <Dot className="size-4" /> : <Circle className="size-2" />}
            </span>
            <div className="min-w-0">
              <p className={cn("text-small", reached ? "text-foreground" : "text-muted")}>{s.label} <span className={cn("ml-1 text-meta", reached ? "text-success" : failed && next ? "text-danger" : next ? "text-warning" : "text-subtle")}>{status}</span></p>
              {!compact && <p className="text-meta text-subtle">{s.detail}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
