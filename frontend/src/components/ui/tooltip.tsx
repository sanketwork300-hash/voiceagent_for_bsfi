"use client";
import * as T from "@radix-ui/react-tooltip";

export const TooltipProvider = T.Provider;
export function Tooltip({ content, children, side = "right" }: { content: React.ReactNode; children: React.ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  return (
    <T.Root delayDuration={250}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content side={side} sideOffset={6} className="z-[var(--z-palette)] rounded-[5px] border border-border-strong bg-raised px-2 py-1 text-meta text-foreground shadow-lg">
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
