import type { Metadata } from "next";
import { AuditLog } from "@/components/audit/audit-log";
import { Suspense } from "react";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Authentication" };

export default function Page() {
  return <RequirePermission perm="audit.read"><Suspense><AuditLog preset={{ prefix: "auth.", title: "Authentication", description: "Every customer verification step — bank logins, OTPs sent, verified and failed, voice checks. OTP values are never recorded." }} /></Suspense></RequirePermission>;
}
