"use client";
import { Lock } from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { Skeleton } from "@/components/ui/states";
import { useProfile } from "@/hooks/use-profile";
import type { Permission } from "@/lib/permissions";

/** UI gate only — the backend independently rejects unauthorised requests. */
export function RequirePermission({ perm, children }: { perm: Permission; children: React.ReactNode }) {
  const { profile, loading, can } = useProfile();
  if (loading || !profile) return <PageContainer><Skeleton className="h-7 w-48" /></PageContainer>;
  if (!can(perm)) {
    return (
      <PageContainer>
        <div className="grid max-w-md gap-2 py-16" role="alert">
          <Lock aria-hidden className="size-5 text-subtle" />
          <h1 className="text-section font-semibold text-strong">You don&apos;t have access to this</h1>
          <p className="text-body text-muted">Your role doesn&apos;t include this area. Ask an administrator if you need it.</p>
        </div>
      </PageContainer>
    );
  }
  return <>{children}</>;
}
