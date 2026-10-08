import type { Metadata } from "next";
import { AgentBuilder } from "@/components/agent-builder/agent-builder";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Agent" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RequirePermission perm="agent.read"><AgentBuilder id={decodeURIComponent(id)} /></RequirePermission>;
}
