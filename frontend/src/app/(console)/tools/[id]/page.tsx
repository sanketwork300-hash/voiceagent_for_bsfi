import type { Metadata } from "next";
import { ToolDetail } from "@/components/tools/tools";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Tool" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RequirePermission perm="agent.read"><ToolDetail name={decodeURIComponent(id)} /></RequirePermission>;
}
