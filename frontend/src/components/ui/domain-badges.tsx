import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import type { AuthState, RiskLevel } from "@/types/domain";
import { Badge } from "./badge";

const RISK_TONE = { LOW: "neutral", MEDIUM: "info", HIGH: "warning", CRITICAL: "danger" } as const;
export function RiskBadge({ level }: { level: RiskLevel }) {
  return <Badge tone={RISK_TONE[level]}>{level.charAt(0) + level.slice(1).toLowerCase()} risk</Badge>;
}

export const AUTH_LABEL: Record<AuthState, string> = {
  UNAUTHENTICATED: "Not verified",
  IDENTIFIED: "Identified",
  PARTIALLY_AUTHENTICATED: "Partially verified",
  FULLY_AUTHENTICATED: "Verified",
  TRANSACTION_AUTHENTICATED: "Transaction verified",
};

export function AuthBadge({ state }: { state: AuthState }) {
  const tone = state === "TRANSACTION_AUTHENTICATED" || state === "FULLY_AUTHENTICATED" ? "success" : state === "UNAUTHENTICATED" ? "neutral" : "warning";
  const Icon = tone === "success" ? ShieldCheck : tone === "warning" ? ShieldAlert : ShieldQuestion;
  return <Badge tone={tone}><Icon aria-hidden className="size-3" />{AUTH_LABEL[state]}</Badge>;
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={`font-mono text-[12.5px] ${className ?? ""}`}>{children}</span>;
}
