"use client";
import * as S from "@radix-ui/react-switch";
import { cn } from "@/utils/cn";

export function Switch({ className, ...p }: S.SwitchProps) {
  return (
    <S.Root className={cn("relative h-5 w-9 shrink-0 rounded-full border border-border-strong bg-raised transition-colors data-[state=checked]:border-strong data-[state=checked]:bg-strong disabled:opacity-40", className)} {...p}>
      <S.Thumb className="block size-3.5 translate-x-0.5 rounded-full bg-muted transition-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-background" />
    </S.Root>
  );
}
