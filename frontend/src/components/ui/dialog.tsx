"use client";
import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/utils/cn";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({ title, description, children, className, side }: {
  title: string; description?: string; children: React.ReactNode; className?: string; side?: "center" | "right" | "bottom" | "left";
}) {
  const pos = side === "left"
    ? "left-0 top-0 h-dvh w-[280px] border-r"
    : side === "right"
    ? "right-0 top-0 h-dvh w-full max-w-md border-l"
    : side === "bottom"
    ? "bottom-0 left-0 max-h-[85dvh] w-full rounded-t-panel border-t"
    : "left-1/2 top-1/2 w-[calc(100%-32px)] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-panel border";
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-[var(--z-overlay)] bg-black/70" />
      <D.Content className={cn("fixed z-[var(--z-modal)] flex flex-col overflow-hidden border-border-strong bg-surface shadow-2xl shadow-black/60 focus:outline-none", pos, className)}>
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div>
            <D.Title className="text-lead font-semibold text-strong">{title}</D.Title>
            {description && <D.Description className="mt-1 text-small text-muted">{description}</D.Description>}
          </div>
          <D.Close aria-label="Close" className="rounded-control p-1 text-muted hover:bg-hover hover:text-foreground"><X className="size-4" /></D.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </D.Content>
    </D.Portal>
  );
}
