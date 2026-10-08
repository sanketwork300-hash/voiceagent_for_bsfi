import type { Metadata } from "next";
import { IntegrationsMarketplace } from "@/components/integrations/integrations";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Integrations" };

export default function Page() {
  return <RequirePermission perm="integration.manage"><IntegrationsMarketplace /></RequirePermission>;
}
