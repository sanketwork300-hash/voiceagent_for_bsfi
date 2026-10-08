"use client";
import * as P from "@radix-ui/react-popover";
import { cn } from "@/utils/cn";

export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;
export function PopoverContent({ className, align = "end", ...p }: P.PopoverContentProps) {
  return (
    <P.Portal>
      <P.Content align={align} sideOffset={6} className={cn("z-[var(--z-modal)] w-80 rounded-panel border border-border-strong bg-surface shadow-xl shadow-black/50 focus:outline-none", className)} {...p} />
    </P.Portal>
  );
}
