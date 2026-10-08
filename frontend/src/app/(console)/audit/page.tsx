import type { Metadata } from "next";
import { AuditLog } from "@/components/audit/audit-log";
import { Suspense } from "react";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Audit logs" };

export default function Page() {
  return <RequirePermission perm="audit.read"><Suspense><AuditLog /></Suspense></RequirePermission>;
}
