import type { Metadata } from "next";
import { SearchLab } from "@/components/knowledge/search-lab";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Knowledge search" };

export default function Page() {
  return <RequirePermission perm="knowledge.read"><SearchLab /></RequirePermission>;
}
