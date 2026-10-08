"use client";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import { can, type Permission } from "@/lib/permissions";

export function useProfile() {
  const q = useQuery({ queryKey: ["me"], queryFn: apiClient.auth.me, staleTime: 60_000 });
  const profile = q.data?.profile ?? null;
  return { profile, loading: q.isLoading, can: (p: Permission) => can(profile?.roles, p) };
}

export function useTenant() {
  const { profile } = useProfile();
  return useQuery({ queryKey: ["tenant", profile?.tenant_id], queryFn: apiClient.tenants.me, enabled: Boolean(profile), staleTime: 300_000 });
}

/** Every tenant-owned query key starts with the tenant id, so a tenant switch can never show cached data from another. */
export function useTenantKey() {
  const { profile } = useProfile();
  return profile?.tenant_id ?? "none";
}
