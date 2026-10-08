import type { Metadata } from "next";
import { OrganizationView } from "@/components/layout/settings";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Organization" };

export default function Page() {
  return <RequirePermission perm="tenant.read"><OrganizationView /></RequirePermission>;
}
