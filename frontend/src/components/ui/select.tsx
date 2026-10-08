"use client";
import * as S from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/utils/cn";

export function Select({ value, onValueChange, options, placeholder, id, className, "aria-label": ariaLabel, disabled }: {
  value?: string; onValueChange: (v: string) => void; options: { value: string; label: string }[]; placeholder?: string;
  id?: string; className?: string; "aria-label"?: string; disabled?: boolean;
}) {
  return (
    <S.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <S.Trigger id={id} aria-label={ariaLabel} className={cn("inline-flex h-8 w-full items-center justify-between gap-2 rounded-control border border-border-strong bg-surface px-2.5 text-body text-foreground data-[placeholder]:text-subtle focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-strong/60", className)}>
        <S.Value placeholder={placeholder} />
        <S.Icon><ChevronDown className="size-4 text-muted" /></S.Icon>
      </S.Trigger>
      <S.Portal>
        <S.Content position="popper" sideOffset={4} className="z-[var(--z-palette)] max-h-72 min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-panel border border-border-strong bg-surface shadow-xl">
          <S.Viewport className="p-1">
            {options.map((o) => (
              <S.Item key={o.value} value={o.value} className="relative flex cursor-default items-center rounded-[5px] py-1.5 pl-7 pr-2 text-small text-foreground outline-none data-[highlighted]:bg-hover">
                <S.ItemIndicator className="absolute left-2"><Check className="size-3.5" /></S.ItemIndicator>
                <S.ItemText>{o.label}</S.ItemText>
              </S.Item>
            ))}
          </S.Viewport>
        </S.Content>
      </S.Portal>
    </S.Root>
  );
}
