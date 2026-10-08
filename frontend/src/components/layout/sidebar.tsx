"use client";
import { ChevronsLeft, ChevronsRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Tooltip } from "@/components/ui/tooltip";
import { NAV, type NavItem } from "@/config/navigation";
import { useProfile, useTenant } from "@/hooks/use-profile";
import { ROLE_LABELS, primaryRole } from "@/lib/permissions";
import { useUi } from "@/store/ui";
import { cn } from "@/utils/cn";

export function isActive(pathname: string, href: string) {
  if (href === "/settings") return pathname === "/settings";
  if (href === "/integrations") return pathname === "/integrations" || /^\/integrations\/(?!credentials)/.test(pathname);
  if (href === "/customers") return pathname === "/customers" || /^\/customers\/(?!authentication)/.test(pathname);
  return pathname === href || pathname.startsWith(href + "/");
}

function NavLink({ item, collapsed, onNavigate }: { item: NavItem; collapsed: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const active = isActive(pathname, item.href);
  const link = (
    <Link href={item.href} onClick={onNavigate} aria-current={active ? "page" : undefined}
      className={cn("group flex h-8 items-center gap-2.5 rounded-control px-2 text-small text-muted transition-colors hover:bg-hover hover:text-foreground",
        active && "bg-raised text-strong shadow-[inset_2px_0_0_var(--strong)]", collapsed && "justify-center px-0")}>
      <item.icon aria-hidden className={cn("size-4 shrink-0", active ? "text-strong" : "text-subtle group-hover:text-foreground")} />
      {collapsed ? <span className="sr-only">{item.label}</span> : <span className="truncate">{item.label}</span>}
    </Link>
  );
  return collapsed ? <Tooltip content={item.label}>{link}</Tooltip> : link;
}

export function SidebarNav({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const { can } = useProfile();
  return (
    <nav aria-label="Primary" className="flex-1 overflow-y-auto px-2 py-3">
      <ul className="grid gap-4">
        {NAV.map((entry) => {
          if (!("items" in entry)) return <li key={entry.href}><NavLink item={entry} collapsed={collapsed} onNavigate={onNavigate} /></li>;
          const items = entry.items.filter((i) => !i.perm || can(i.perm));
          if (!items.length) return null;
          return (
            <li key={entry.label}>
              {!collapsed && <p className="px-2 pb-1 text-meta text-subtle">{entry.label}</p>}
              {collapsed && <div aria-hidden className="mx-3 mb-2 h-px bg-border" />}
              <ul className="grid gap-0.5">{items.map((i) => <li key={i.href}><NavLink item={i} collapsed={collapsed} onNavigate={onNavigate} /></li>)}</ul>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function SidebarFooter({ collapsed }: { collapsed: boolean }) {
  const { profile } = useProfile();
  const tenant = useTenant();
  const initials = (profile?.email ?? "?").slice(0, 2).toUpperCase();
  return (
    <div className={cn("border-t border-border p-3", collapsed && "px-2")}>
      {!collapsed && <p className="mb-2 truncate text-meta text-subtle">{tenant.data?.name ?? "Organization"}</p>}
      <div className={cn("flex items-center gap-2.5", collapsed && "justify-center")}>
        <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full border border-border-strong bg-raised text-[11px] font-medium text-foreground">{initials}</span>
        {!collapsed && (
          <div className="min-w-0">
            <p className="truncate text-small text-foreground">{profile?.email ?? "—"}</p>
            <p className="truncate text-meta text-muted">{ROLE_LABELS[primaryRole(profile?.roles ?? [])] ?? "—"}</p>
          </div>
        )}
      </div>
    </div>
  );
}

export function Sidebar() {
  const { sidebarCollapsed: collapsed, toggleSidebar } = useUi();
  return (
    <aside className={cn("hidden shrink-0 flex-col border-r border-border bg-background transition-[width] duration-[var(--dur)] lg:flex", collapsed ? "w-14" : "w-60")}>
      <div className={cn("flex h-12 items-center border-b border-border", collapsed ? "justify-center" : "justify-between px-3")}>
        {!collapsed && <Wordmark />}
        <button onClick={toggleSidebar} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}
          className="rounded-control p-1.5 text-subtle hover:bg-hover hover:text-foreground">
          {collapsed ? <ChevronsRight className="size-4" /> : <ChevronsLeft className="size-4" />}
        </button>
      </div>
      <SidebarNav collapsed={collapsed} />
      <SidebarFooter collapsed={collapsed} />
    </aside>
  );
}

export function Wordmark() {
  return (
    <Link href="/dashboard" className="flex items-center gap-2 text-small font-semibold tracking-[-0.01em] text-strong">
      <svg aria-hidden viewBox="0 0 20 20" className="size-5"><rect x="1" y="1" width="18" height="18" rx="4" fill="none" stroke="currentColor" strokeWidth="1.25" />
        <path d="M5 13V7m3.3 6V5m3.4 8V8.5M15 13v-3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
      Ledgerline
    </Link>
  );
}
