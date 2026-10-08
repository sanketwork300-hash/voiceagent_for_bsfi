import type { Metadata } from "next";
import { AgentList } from "@/components/agent-builder/agent-builder";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Agents" };

export default function Page() {
  return <RequirePermission perm="agent.read"><AgentList /></RequirePermission>;
}
