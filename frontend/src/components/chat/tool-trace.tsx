"use client";
import { Check, ChevronRight, Loader2, ShieldAlert, X } from "lucide-react";
import { useState } from "react";
import { TOOL_LABELS, toolLabel } from "@/config/tools";
import type { ToolStep } from "@/lib/chat/conversation";
import { formatMs } from "@/utils/format";
import { cn } from "@/utils/cn";

const SOURCE_LABEL: Record<string, string> = { openapi: "Bank API (OpenAPI)", rest: "Bank API (REST)", mcp: "MCP server", builtin: "Platform", adapter: "Adapter" };

function StepIcon({ s }: { s: ToolStep }) {
  if (s.status === "running") return <Loader2 aria-label="In progress" className="size-3.5 animate-spin text-muted" />;
  if (s.status === "completed") return <Check aria-label="Done" className="size-3.5 text-success" />;
  if (s.status === "held") return <ShieldAlert aria-label="Waiting on policy" className="size-3.5 text-info" />;
  if (s.policyDecision === "DENY") return <ShieldAlert aria-label="Blocked by policy" className="size-3.5 text-warning" />;
  return <X aria-label="Failed" className="size-3.5 text-danger" />;
}

/** Customers see plain-language services used; operators can expand to the execution trace (never secrets). */
export function ToolTrace({ tools, audience }: { tools: ToolStep[]; audience: "customer" | "operator" }) {
  const [open, setOpen] = useState(false);
  if (!tools.length) return null;
  const running = tools.some((t) => t.status === "running");
  if (audience === "customer") {
    // held steps are represented by the action slip; only show services actually used
    const services = Array.from(new Set(tools.filter((t) => t.tool !== "request_human_handoff" && t.status !== "held").map((t) => TOOL_LABELS[t.tool]?.service ?? "Bank service")));
    if (!services.length) return null;
    return (
      <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-meta text-muted">
        <span>{running ? "Checking" : "Used"}</span>
        {services.map((s) => <span key={s} className="inline-flex items-center gap-1">{running ? <Loader2 aria-hidden className="size-3 animate-spin" /> : <Check aria-hidden className="size-3 text-success" />}{s}</span>)}
      </p>
    );
  }
  return (
    <div className="mt-2 text-meta">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="inline-flex items-center gap-1.5 text-muted hover:text-foreground">
        <ChevronRight aria-hidden className={cn("size-3 transition-transform", open && "rotate-90")} />
        {tools.length} tool call{tools.length > 1 ? "s" : ""} · {tools.map((t) => ({ completed: "✓", running: "…", held: "◇", failed: "✕" })[t.status] + " " + t.tool).join("  ")}
      </button>
      {open && (
        <ol className="mt-2 grid gap-2 border-l border-border pl-3">
          {tools.map((t) => (
            <li key={t.id} className="grid gap-1">
              <div className="flex items-center gap-2"><StepIcon s={t} /><span className="font-mono text-[12px] text-foreground">{t.tool}</span><span className="text-muted">{toolLabel(t.tool, t.status !== "running")}</span></div>
              <dl className="grid grid-cols-[80px_1fr] gap-x-3 text-muted">
                <dt>Status</dt><dd className="text-foreground">{t.status}{t.policyDecision && t.policyDecision !== "ALLOW" ? ` · ${t.policyDecision}` : ""}{t.outcome ? ` · ${t.outcome.replace("_", " ")}` : ""}</dd>
                <dt>Latency</dt><dd className="font-mono">{formatMs(t.latencyMs)}</dd>
                {t.parallelGroup != null && <><dt>Wave</dt><dd className="font-mono">{t.parallelGroup}{tools.filter((o) => o.parallelGroup === t.parallelGroup).length > 1 ? " · parallel" : ""}</dd></>}
                {t.verification && <><dt>Verified</dt><dd className={t.verification === "SUCCESS" ? "text-success" : "text-warning"}>{t.verification.toLowerCase()}</dd></>}
                {t.source && <><dt>Source</dt><dd>{SOURCE_LABEL[t.source] ?? t.source}</dd></>}
                {t.error && <><dt>Error</dt><dd className="text-warning">{t.error}</dd></>}
              </dl>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
