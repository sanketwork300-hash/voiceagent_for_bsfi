import type { Metadata } from "next";
import { CustomerDetail } from "@/components/customer/customers";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Customer" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RequirePermission perm="customer.read"><CustomerDetail id={decodeURIComponent(id)} /></RequirePermission>;
}
