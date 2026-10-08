import type { Metadata } from "next";
import { IntegrationDetail } from "@/components/integrations/integrations";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Integration" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RequirePermission perm="integration.manage"><IntegrationDetail id={decodeURIComponent(id)} /></RequirePermission>;
}
