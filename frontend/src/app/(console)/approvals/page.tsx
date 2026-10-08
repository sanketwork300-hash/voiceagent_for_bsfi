import type { Metadata } from "next";
import { ApprovalCenter } from "@/components/approvals/approval-center";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Approvals" };

export default function Page() {
  return <RequirePermission perm="approval.execute"><ApprovalCenter /></RequirePermission>;
}
