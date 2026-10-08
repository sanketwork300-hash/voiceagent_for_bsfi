"use client";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, ChevronDown, CircleHelp, LogOut, Menu as MenuIcon, Search, Settings, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { Kbd } from "@/components/ui/page";
import { ENVIRONMENT, environmentUrls, type Environment } from "@/config/env";
import { useProfile, useTenant } from "@/hooks/use-profile";
import { apiClient } from "@/lib/api/client";
import { ROLE_LABELS, primaryRole } from "@/lib/permissions";
import { useCustomerSession } from "@/store/customer-session";
import { useUi } from "@/store/ui";
import { cn } from "@/utils/cn";
import { NotificationCenter } from "./notification-center";

const ENV_LABEL: Record<Environment, string> = { development: "Development", staging: "Staging", production: "Production" };

export function useSignOut() {
  const qc = useQueryClient();
  const router = useRouter();
  const clearCustomer = useCustomerSession((s) => s.clear);
  return async () => {
    await apiClient.auth.logout().catch(() => undefined);
    qc.clear(); // drop every cached tenant-scoped query
    clearCustomer();
    router.replace("/auth/login");
  };
}

export function TopBar() {
  const { profile } = useProfile();
  const tenant = useTenant();
  const setPalette = useUi((s) => s.setPalette);
  const setMobileNav = useUi((s) => s.setMobileNav);
  const signOut = useSignOut();
  const urls = environmentUrls();
  const isProd = ENVIRONMENT === "production";
  return (
    <header className={cn("sticky top-0 z-[var(--z-sticky)] flex h-12 shrink-0 items-center gap-2 border-b border-border bg-background/95 px-3 backdrop-blur supports-[backdrop-filter]:bg-background/80", isProd && "prod-stripe")}>
      <button className="rounded-control p-1.5 text-muted hover:bg-hover hover:text-foreground lg:hidden" aria-label="Open navigation" onClick={() => setMobileNav(true)}>
        <MenuIcon className="size-4" />
      </button>
      <Menu>
        <MenuTrigger className="flex min-w-0 max-w-[44vw] items-center gap-2 rounded-control px-2 py-1 text-small text-foreground hover:bg-hover">
          <Building2 aria-hidden className="size-4 text-subtle" />
          <span className="truncate font-medium">{tenant.data?.name ?? profile?.tenant_slug ?? "Organization"}</span>
          <ChevronDown aria-hidden className="size-3.5 text-subtle" />
        </MenuTrigger>
        <MenuContent align="start" className="w-64">
          <MenuLabel>Signed in to</MenuLabel>
          <MenuItem disabled><Building2 />{tenant.data?.name ?? "—"}<span className="ml-auto text-meta text-muted">{ENV_LABEL[ENVIRONMENT]}</span></MenuItem>
          <MenuSeparator />
          <MenuItem onSelect={signOut}>Switch organization…</MenuItem>
          <p className="px-2 pb-1.5 text-meta text-muted">Each organization is isolated. Switching signs you out and clears cached data.</p>
        </MenuContent>
      </Menu>
      <Menu>
        <MenuTrigger aria-label={`Environment: ${ENV_LABEL[ENVIRONMENT]}`} className="hidden rounded-control px-1 py-1 hover:bg-hover sm:block">
          <Badge tone={isProd ? "warning" : "neutral"}>{isProd && <ShieldAlert aria-hidden className="size-3" />}{ENV_LABEL[ENVIRONMENT]}</Badge>
        </MenuTrigger>
        <MenuContent align="start">
          <MenuLabel>Environment</MenuLabel>
          {(Object.keys(ENV_LABEL) as Environment[]).map((env) => (
            <MenuItem key={env} disabled={env === ENVIRONMENT || !urls[env]} onSelect={() => urls[env] && (window.location.href = urls[env]!)}>
              {ENV_LABEL[env]}{env === ENVIRONMENT && <span className="ml-auto text-meta text-muted">Current</span>}
              {env !== ENVIRONMENT && !urls[env] && <span className="ml-auto text-meta text-muted">Not configured</span>}
            </MenuItem>
          ))}
        </MenuContent>
      </Menu>
      <button onClick={() => setPalette(true)} className="ml-auto flex h-8 w-9 items-center justify-center gap-2 rounded-control border border-border-strong bg-surface text-small text-muted hover:text-foreground sm:w-72 sm:justify-start sm:px-2.5"
        aria-label="Search (Ctrl K)">
        <Search aria-hidden className="size-4" /><span className="hidden sm:inline">Search customers, tools, logs…</span>
        <span className="ml-auto hidden sm:inline"><Kbd>Ctrl K</Kbd></span>
      </button>
      <NotificationCenter />
      <Menu>
        <MenuTrigger aria-label="Help" className="hidden rounded-control p-1.5 text-muted hover:bg-hover hover:text-foreground sm:block"><CircleHelp className="size-4" /></MenuTrigger>
        <MenuContent>
          <MenuItem onSelect={() => setPalette(true)}>Command palette <span className="ml-auto"><Kbd>Ctrl K</Kbd></span></MenuItem>
          <MenuItem asChild><Link href="/settings">Keyboard &amp; accessibility</Link></MenuItem>
          <MenuItem asChild><a href="/api/backend/docs" target="_blank" rel="noreferrer">API reference</a></MenuItem>
        </MenuContent>
      </Menu>
      <Menu>
        <MenuTrigger aria-label="Account" className="grid size-7 place-items-center rounded-full border border-border-strong bg-raised text-[11px] font-medium hover:border-[#3a3a3a]">
          {(profile?.email ?? "?").slice(0, 2).toUpperCase()}
        </MenuTrigger>
        <MenuContent className="w-60">
          <MenuLabel><span className="block truncate text-small text-foreground">{profile?.email}</span>{ROLE_LABELS[primaryRole(profile?.roles ?? [])]}</MenuLabel>
          <MenuSeparator />
          <MenuItem asChild><Link href="/settings"><Settings />Settings &amp; security</Link></MenuItem>
          <MenuItem onSelect={signOut}><LogOut />Sign out</MenuItem>
        </MenuContent>
      </Menu>
    </header>
  );
}
