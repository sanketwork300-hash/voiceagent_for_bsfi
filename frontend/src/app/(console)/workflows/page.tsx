import type { Metadata } from "next";
import { WorkflowsView } from "@/components/agent-builder/workflows";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Workflows" };

export default function Page() {
  return <RequirePermission perm="agent.read"><WorkflowsView /></RequirePermission>;
}
