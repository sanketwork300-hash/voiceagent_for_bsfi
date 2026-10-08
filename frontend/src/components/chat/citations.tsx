"use client";
import { ChevronRight, FileText } from "lucide-react";
import { useState } from "react";
import type { SourceCitation } from "@/types/domain";
import { cn } from "@/utils/cn";

export function Citations({ sources, audience }: { sources: SourceCitation[]; audience: "customer" | "operator" }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!sources.length) return null;
  return (
    <div className="mt-3 grid gap-1">
      <p className="text-meta text-muted">Sources</p>
      <ol className="grid gap-1">
        {sources.map((s) => (
          <li key={s.chunk_id} id={`source-${s.index}`}>
            <button onClick={() => setOpen(open === s.index ? null : s.index)} aria-expanded={open === s.index}
              className="flex w-full items-start gap-2 rounded-control px-1.5 py-1 text-left text-small hover:bg-hover">
              <span className="mt-px grid size-4 shrink-0 place-items-center rounded-[3px] border border-border-strong font-mono text-[10px] text-muted">{s.index}</span>
              <FileText aria-hidden className="mt-0.5 size-3.5 shrink-0 text-subtle" />
              <span className="min-w-0 flex-1 text-foreground">{s.title}{s.version ? ` · v${s.version}` : ""}{s.page ? ` · page ${s.page}` : ""}
                {s.section && <span className="block truncate text-meta text-muted">{s.section}</span>}</span>
              {audience === "operator" && <span className="font-mono text-meta text-muted" title="Rerank score">{s.score.toFixed(3)}</span>}
              <ChevronRight aria-hidden className={cn("mt-0.5 size-3.5 shrink-0 text-subtle transition-transform", open === s.index && "rotate-90")} />
            </button>
            {open === s.index && (
              <blockquote className="mb-1 ml-8 border-l border-border-strong pl-3 text-small text-muted">
                {s.snippet.length > 480 ? s.snippet.slice(0, 480) + "…" : s.snippet}
                {audience === "operator" && <span className="mt-1 block font-mono text-meta text-subtle">chunk {s.chunk_id}</span>}
              </blockquote>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
