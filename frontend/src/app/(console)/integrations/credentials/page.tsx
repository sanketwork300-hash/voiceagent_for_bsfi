import type { Metadata } from "next";
import { CredentialsView } from "@/components/integrations/integrations";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Credentials" };

export default function Page() {
  return <RequirePermission perm="integration.manage"><CredentialsView /></RequirePermission>;
}
