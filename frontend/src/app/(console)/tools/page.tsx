import type { Metadata } from "next";
import { ToolRegistry } from "@/components/tools/tools";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Tools" };

export default function Page() {
  return <RequirePermission perm="agent.read"><ToolRegistry /></RequirePermission>;
}
