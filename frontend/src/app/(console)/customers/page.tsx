import type { Metadata } from "next";
import { CustomerList } from "@/components/customer/customers";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Customers" };

export default function Page() {
  return <RequirePermission perm="customer.read"><CustomerList /></RequirePermission>;
}
