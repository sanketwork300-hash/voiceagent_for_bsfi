import type { Metadata } from "next";
import { PolicyList } from "@/components/policies/policies";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Policies" };

export default function Page() {
  return <RequirePermission perm="policy.manage"><PolicyList /></RequirePermission>;
}
