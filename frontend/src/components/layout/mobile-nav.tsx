"use client";
import { MoreHorizontal } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { MOBILE_NAV } from "@/config/navigation";
import { useProfile } from "@/hooks/use-profile";
import { useUi } from "@/store/ui";
import { cn } from "@/utils/cn";
import { SidebarFooter, SidebarNav, isActive } from "./sidebar";

export function MobileDrawer() {
  const { mobileNavOpen, setMobileNav } = useUi();
  return (
    <Dialog open={mobileNavOpen} onOpenChange={setMobileNav}>
      <DialogContent title="Navigation" side="left" className="p-0">
        <div className="-mx-5 -my-4 flex h-full flex-col">
          <SidebarNav onNavigate={() => setMobileNav(false)} />
          <SidebarFooter collapsed={false} />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function BottomNav() {
  const pathname = usePathname();
  const { can } = useProfile();
  const setMobileNav = useUi((s) => s.setMobileNav);
  if (pathname === "/voice") return null; // voice is full-screen on mobile
  return (
    <nav aria-label="Primary (mobile)" className="fixed inset-x-0 bottom-0 z-[var(--z-sticky)] border-t border-border bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
      <ul className="grid grid-cols-5">
        {MOBILE_NAV.filter((i) => !i.perm || can(i.perm)).map((i) => {
          const active = isActive(pathname, i.href);
          return (
            <li key={i.href}>
              <Link href={i.href} aria-current={active ? "page" : undefined} className={cn("flex flex-col items-center gap-0.5 py-2 text-[11px] text-muted", active && "text-strong")}>
                <i.icon aria-hidden className="size-5" />{i.label}
              </Link>
            </li>
          );
        })}
        <li><button onClick={() => setMobileNav(true)} className="flex w-full flex-col items-center gap-0.5 py-2 text-[11px] text-muted"><MoreHorizontal aria-hidden className="size-5" />More</button></li>
      </ul>
    </nav>
  );
}
