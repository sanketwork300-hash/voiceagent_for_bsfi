import type { Metadata } from "next";
import { RolesView } from "@/components/layout/settings";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Roles" };

export default function Page() {
  return <RequirePermission perm="tenant.read"><RolesView /></RequirePermission>;
}
