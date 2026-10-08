import type { Metadata } from "next";
import { ConversationsList } from "@/components/customer/conversations";
import { Suspense } from "react";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Conversations" };

export default function Page() {
  return <RequirePermission perm="conversation.read"><Suspense><ConversationsList mode="conversations" /></Suspense></RequirePermission>;
}
