"use client";
import { ArrowUp, AudioLines, Square } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/utils/cn";

export function Composer({ onSend, onVoice, onStop, busy, disabled, placeholder, hint }: {
  onSend: (t: string) => void; onVoice?: () => void; onStop?: () => void; busy: boolean; disabled?: boolean; placeholder?: string; hint?: string;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const submit = () => {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText("");
    requestAnimationFrame(() => { if (ref.current) ref.current.style.height = "auto"; });
  };
  return (
    <form className="border-t border-border bg-background px-3 pb-3 pt-2 sm:px-4" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      {hint && <p className="mb-1.5 px-1 text-meta text-muted">{hint}</p>}
      <div className={cn("flex items-end gap-2 rounded-[10px] border border-border-strong bg-surface p-1.5 pl-3 focus-within:border-[#4a4a4a]", disabled && "opacity-60")}>
        <label htmlFor="composer" className="sr-only">Message</label>
        <textarea id="composer" ref={ref} rows={1} value={text} disabled={disabled} maxLength={4000}
          placeholder={placeholder ?? "Ask about balances, loans, cards, payments…"}
          onChange={(e) => { setText(e.target.value); e.target.style.height = "auto"; e.target.style.height = `${Math.min(160, e.target.scrollHeight)}px`; }}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}
          className="max-h-40 min-h-8 flex-1 resize-none bg-transparent py-1.5 text-body text-foreground placeholder:text-subtle focus:outline-none" />
        {onVoice && <Button type="button" variant="ghost" size="icon" aria-label="Switch to voice" onClick={onVoice}><AudioLines /></Button>}
        {busy && onStop
          ? <Button type="button" size="icon" aria-label="Stop response" onClick={onStop}><Square className="fill-current" /></Button>
          : <Button type="submit" variant="primary" size="icon" aria-label="Send message" disabled={!text.trim() || disabled}><ArrowUp /></Button>}
      </div>
    </form>
  );
}
