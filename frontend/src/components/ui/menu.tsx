"use client";
import * as M from "@radix-ui/react-dropdown-menu";
import { cn } from "@/utils/cn";

export const Menu = M.Root;
export const MenuTrigger = M.Trigger;
export function MenuContent({ className, align = "end", ...props }: M.DropdownMenuContentProps) {
  return (
    <M.Portal>
      <M.Content align={align} sideOffset={6} className={cn("z-[var(--z-modal)] min-w-48 rounded-panel border border-border-strong bg-surface p-1 shadow-xl shadow-black/50", className)} {...props} />
    </M.Portal>
  );
}
export function MenuItem({ className, ...props }: M.DropdownMenuItemProps) {
  return <M.Item className={cn("flex cursor-default items-center gap-2 rounded-[5px] px-2 py-1.5 text-small text-foreground outline-none data-[highlighted]:bg-hover data-[disabled]:opacity-40 [&_svg]:size-4 [&_svg]:text-muted", className)} {...props} />;
}
export function MenuLabel({ className, ...props }: M.DropdownMenuLabelProps) {
  return <M.Label className={cn("px-2 py-1.5 text-meta text-muted", className)} {...props} />;
}
export const MenuSeparator = () => <M.Separator className="my-1 h-px bg-border" />;
