import type { Metadata } from "next";
import { HandoffDesk } from "@/components/handoff/handoff-desk";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Cases" };

export default function Page() {
  return <RequirePermission perm="handoff.handle"><HandoffDesk title="Cases" description="Escalated conversations handled by specialists, with the AI's context." /></RequirePermission>;
}
