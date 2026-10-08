"use client";
import * as T from "@radix-ui/react-tabs";
import { cn } from "@/utils/cn";

export const Tabs = T.Root;
export function TabsList({ className, ...p }: T.TabsListProps) {
  return <T.List className={cn("flex gap-1 overflow-x-auto border-b border-border", className)} {...p} />;
}
export function TabsTrigger({ className, ...p }: T.TabsTriggerProps) {
  return <T.Trigger className={cn("-mb-px whitespace-nowrap border-b border-transparent px-3 py-2 text-small text-muted hover:text-foreground data-[state=active]:border-strong data-[state=active]:text-strong", className)} {...p} />;
}
export function TabsContent({ className, ...p }: T.TabsContentProps) {
  return <T.Content className={cn("pt-5 focus-visible:outline-none", className)} {...p} />;
}
