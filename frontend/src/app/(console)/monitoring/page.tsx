import type { Metadata } from "next";
import { RequirePermission } from "@/components/layout/require-permission";
import { MonitoringView } from "@/components/monitoring/overview";

export const metadata: Metadata = { title: "Monitoring" };
export default function Page() { return <RequirePermission perm="tenant.read"><MonitoringView /></RequirePermission>; }
