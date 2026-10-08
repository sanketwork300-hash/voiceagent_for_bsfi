import type { Metadata } from "next";
import { PolicyEditor } from "@/components/policies/policies";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Policy" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RequirePermission perm="policy.manage"><PolicyEditor id={decodeURIComponent(id)} /></RequirePermission>;
}
