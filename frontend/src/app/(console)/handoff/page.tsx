import type { Metadata } from "next";
import { HandoffDesk } from "@/components/handoff/handoff-desk";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Human handoff" };

export default function Page() {
  return <RequirePermission perm="handoff.handle"><HandoffDesk /></RequirePermission>;
}
