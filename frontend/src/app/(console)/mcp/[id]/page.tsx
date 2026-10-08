import type { Metadata } from "next";
import { McpServerDetail } from "@/components/mcp/mcp";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "MCP server" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RequirePermission perm="mcp.manage"><McpServerDetail id={decodeURIComponent(id)} /></RequirePermission>;
}
