"use client";
import { Delete } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";

/** On-screen keypad for OTPs during a call. Digits are masked and never written to the transcript. */
export function Keypad({ onSubmit, onClose }: { onSubmit: (digits: string) => void; onClose: () => void }) {
  const [digits, setDigits] = useState("");
  const press = (d: string) => setDigits((x) => (x.length < 8 ? x + d : x));
  return (
    <div className="grid gap-4">
      <p aria-live="polite" className="h-9 text-center font-mono text-[26px] tracking-[0.4em] text-strong">{"•".repeat(digits.length) || <span className="text-small tracking-normal text-subtle">Enter the code</span>}</p>
      <div className="grid grid-cols-3 gap-2" role="group" aria-label="Keypad">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
          <button key={d} onClick={() => press(d)} className="h-12 rounded-control border border-border-strong bg-raised font-mono text-lead text-foreground hover:bg-hover" aria-label={`Digit ${d}`}>{d}</button>
        ))}
        <button onClick={() => setDigits((x) => x.slice(0, -1))} className="grid h-12 place-items-center rounded-control text-muted hover:bg-hover" aria-label="Delete digit"><Delete className="size-5" /></button>
        <button onClick={() => press("0")} className="h-12 rounded-control border border-border-strong bg-raised font-mono text-lead text-foreground hover:bg-hover" aria-label="Digit 0">0</button>
        <span />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Close</Button>
        <Button variant="primary" disabled={digits.length < 4} onClick={() => { onSubmit(digits); setDigits(""); onClose(); }}>Send code</Button>
      </div>
    </div>
  );
}
