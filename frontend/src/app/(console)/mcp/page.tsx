import type { Metadata } from "next";
import { McpServers } from "@/components/mcp/mcp";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "MCP servers" };

export default function Page() {
  return <RequirePermission perm="mcp.manage"><McpServers /></RequirePermission>;
}
