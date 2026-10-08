import { cn } from "@/utils/cn";

const TONES = { success: "bg-success", warning: "bg-warning", danger: "bg-danger", info: "bg-info", neutral: "bg-subtle", voice: "bg-voice" } as const;
export type DotTone = keyof typeof TONES;

/** Status indicator. Colour is never the only signal: always pair with a text label. */
export function StatusDot({ tone = "neutral", pulse, className, label }: { tone?: DotTone; pulse?: boolean; className?: string; label?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", TONES[tone], pulse && "animate-pulse-dot")} />
      {label && <span>{label}</span>}
    </span>
  );
}

export function healthTone(status: string | null | undefined): DotTone {
  switch ((status ?? "").toLowerCase()) {
    case "healthy": case "connected": case "active": case "completed": case "success": case "indexed": case "approved": case "operational":
      return "success";
    case "degraded": case "pending": case "queued": case "draft": case "assigned": case "require_confirmation": case "require_auth":
      return "warning";
    case "down": case "failed": case "failure": case "denied": case "rejected": case "blocked": case "deny":
      return "danger";
    default:
      return "neutral";
  }
}
