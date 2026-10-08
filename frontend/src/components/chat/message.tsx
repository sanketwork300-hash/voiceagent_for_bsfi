"use client";
import { Check, Copy, Flag, RotateCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { memo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/input";
import { ACTION_TOOLS } from "@/config/tools";
import { submitFeedback } from "@/lib/api/mock/feedback";
import type { ChatMessage } from "@/lib/chat/conversation";
import { formatTime } from "@/utils/format";
import { cn } from "@/utils/cn";
import { ActionSlip } from "./action-slip";
import { Citations } from "./citations";
import { ToolTrace } from "./tool-trace";

type SlipActions = React.ComponentProps<typeof ActionSlip>["actions"];

/** Markdown without raw HTML (react-markdown escapes it), safe links, and [n] citation markers as chips. */
export const AgentMarkdown = memo(function AgentMarkdown({ text }: { text: string }) {
  const withCites = text.replace(/\[(\d{1,2})\](?!\()/g, "[$1](#source-$1)");
  return (
    <div className="prose-agent text-body leading-relaxed text-foreground">
      <ReactMarkdown remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => href?.startsWith("#source-")
            ? <a href={href} className="mx-0.5 inline-grid h-4 min-w-4 place-items-center rounded-[3px] border border-border-strong px-1 align-[1px] font-mono text-[10px] text-muted no-underline hover:text-foreground" aria-label={`Source ${children}`}>{children}</a>
            : <a href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>,
          img: () => null,
        }}>
        {withCites}
      </ReactMarkdown>
    </div>
  );
});

function Thinking({ label }: { label?: string | null }) {
  return (
    <p role="status" className="flex items-center gap-2 text-small text-muted">
      <span aria-hidden className="flex gap-0.5">{[0, 1, 2].map((i) => <span key={i} className="size-1 rounded-full bg-muted animate-pulse-dot" style={{ animationDelay: `${i * 160}ms` }} />)}</span>
      {label ?? "Working on it"}…
    </p>
  );
}

export function MessageView({ m, audience, slipActions, busy, onRegenerate }: {
  m: ChatMessage; audience: "customer" | "operator"; slipActions: SlipActions; busy: boolean; onRegenerate?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [report, setReport] = useState("");

  if (m.role === "customer") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[min(560px,85%)]">
          <div className={cn("rounded-[12px] rounded-br-[4px] bg-raised px-3.5 py-2 text-body text-foreground", m.masked && "text-muted italic")}>
            {m.masked ? "One-time password entered" : m.text}
          </div>
          <p className="mt-1 text-right text-meta text-subtle">{formatTime(m.at, false)}</p>
        </div>
      </div>
    );
  }
  if (m.role === "human") {
    return (
      <div className="max-w-[min(640px,92%)] border-l-2 border-info/60 pl-3">
        <p className="mb-1 text-meta text-info">Specialist · {formatTime(m.at, false)}</p>
        <p className="whitespace-pre-wrap text-body text-foreground">{m.text}</p>
      </div>
    );
  }
  const involvesAction = m.tools.some((t) => ACTION_TOOLS[t.tool]) || m.slips.length > 0;
  // OTP / confirmation / approval prompts: the slip carries the action, the text is secondary
  const procedural = m.slips.some((s) => s.kind === "confirm" || s.kind === "verify" || s.kind === "approval");
  const done = m.status === "done";
  return (
    <article className="max-w-[min(680px,96%)]" aria-label="Assistant message" aria-busy={m.status === "thinking" || m.status === "streaming"}>
      <p className="mb-1 text-meta text-subtle">Assistant · {formatTime(m.at, false)}</p>
      {m.status === "thinking" && !m.text && <Thinking label={m.statusLabel} />}
      {m.text && (procedural ? <p className="text-small text-muted">{m.text}</p> : <AgentMarkdown text={m.text} />)}
      {m.status === "streaming" && <span aria-hidden className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse-dot bg-foreground" />}
      {m.status === "interrupted" && <p className="mt-1 text-meta text-muted">Stopped.</p>}
      {m.status === "error" && m.reference && <p className="mt-1 font-mono text-meta text-subtle">Reference {m.reference}</p>}
      <ToolTrace tools={m.tools} audience={audience} />
      <Citations sources={m.sources} audience={audience} />
      {m.slips.map((s) => <ActionSlip key={s.id} slip={s} actions={slipActions} busy={busy} />)}
      {done && m.text && !procedural && (
        <div className="mt-2 flex items-center gap-0.5 text-subtle">
          <Button size="icon-sm" variant="ghost" aria-label="Copy message" onClick={async () => { await navigator.clipboard.writeText(m.text); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
            {copied ? <Check /> : <Copy />}
          </Button>
          {onRegenerate && !involvesAction && <Button size="icon-sm" variant="ghost" aria-label="Ask again" onClick={onRegenerate}><RotateCw /></Button>}
          <Button size="icon-sm" variant="ghost" aria-label="Helpful" aria-pressed={rating === "up"} className={cn(rating === "up" && "text-strong")}
            onClick={() => { setRating("up"); void submitFeedback({ messageId: m.id, rating: "up" }); }}><ThumbsUp /></Button>
          <Button size="icon-sm" variant="ghost" aria-label="Not helpful" aria-pressed={rating === "down"} className={cn(rating === "down" && "text-strong")}
            onClick={() => { setRating("down"); void submitFeedback({ messageId: m.id, rating: "down" }); }}><ThumbsDown /></Button>
          <Button size="icon-sm" variant="ghost" aria-label="Report an issue" onClick={() => setReportOpen(true)}><Flag /></Button>
          {rating && <span className="ml-1 text-meta text-muted">Thanks for the feedback.</span>}
        </div>
      )}
      <Dialog open={reportOpen} onOpenChange={setReportOpen}>
        <DialogContent title="Report an issue" description="Tell us what was wrong with this answer. Don't include passwords, PINs or one-time codes.">
          <form className="grid gap-3" onSubmit={async (e) => { e.preventDefault(); await submitFeedback({ messageId: m.id, rating: "down", comment: report, kind: "issue" }); setReportOpen(false); setReport(""); setRating("down"); }}>
            <Textarea aria-label="Describe the issue" value={report} onChange={(e) => setReport(e.target.value)} maxLength={1000} required />
            <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={() => setReportOpen(false)}>Cancel</Button><Button type="submit" variant="primary">Send report</Button></div>
          </form>
        </DialogContent>
      </Dialog>
    </article>
  );
}
