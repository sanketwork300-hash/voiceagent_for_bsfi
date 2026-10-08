import type { Metadata } from "next";
import { DocumentList } from "@/components/knowledge/documents";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Documents" };

export default function Page() {
  return <RequirePermission perm="knowledge.read"><DocumentList /></RequirePermission>;
}
