import type { Metadata } from "next";
import { UsersView } from "@/components/layout/settings";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Users" };

export default function Page() {
  return <RequirePermission perm="user.manage"><UsersView /></RequirePermission>;
}
