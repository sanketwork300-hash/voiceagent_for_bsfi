import type { Metadata } from "next";
import { DocumentDetail } from "@/components/knowledge/documents";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Document" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RequirePermission perm="knowledge.read"><DocumentDetail id={decodeURIComponent(id)} /></RequirePermission>;
}
