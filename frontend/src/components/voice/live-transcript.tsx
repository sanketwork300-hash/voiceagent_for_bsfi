"use client";
import { useEffect, useRef } from "react";
import type { TranscriptEntry } from "@/lib/voice/browser-transport";
import { formatTime } from "@/utils/format";
import { cn } from "@/utils/cn";

const SPEAKER = { customer: "Customer", agent: "Assistant", system: "System" } as const;

export function LiveTranscript({ entries, you = false }: { entries: TranscriptEntry[]; you?: boolean }) {
  const end = useRef<HTMLLIElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [entries]);
  return (
    <section aria-label="Live transcript" className="flex min-h-0 flex-col">
      <h2 className="border-b border-border px-4 py-2.5 text-small font-medium text-strong">Live transcript</h2>
      <ol className="min-h-0 flex-1 overflow-y-auto px-4 py-3" aria-live="polite">
        {entries.length === 0 && <li className="text-small text-muted">What you and the assistant say will appear here.</li>}
        {entries.map((e) => (
          <li key={e.id} className={cn("grid grid-cols-[64px_1fr] gap-x-3 py-1.5", e.speaker === "system" && "text-muted")}>
            <span className="font-mono text-meta text-subtle tabular">{formatTime(e.at)}</span>
            <div className="min-w-0">
              <p className={cn("text-meta", e.speaker === "agent" ? "text-voice" : e.speaker === "customer" ? "text-foreground" : "text-subtle")}>
                {e.speaker === "customer" && you ? "You" : SPEAKER[e.speaker]}{!e.final && " · speaking…"}
              </p>
              <p className={cn("text-small", e.final ? "text-foreground" : "text-muted italic", e.speaker === "system" && "text-muted")}>{e.text}</p>
            </div>
          </li>
        ))}
        <li ref={end} aria-hidden className="h-px" />
      </ol>
    </section>
  );
}
