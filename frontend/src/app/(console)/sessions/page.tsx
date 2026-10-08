import type { Metadata } from "next";
import { ConversationsList } from "@/components/customer/conversations";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Sessions" };

export default function Page() {
  return <RequirePermission perm="conversation.read"><ConversationsList mode="sessions" /></RequirePermission>;
}
