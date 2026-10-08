import type { Metadata } from "next";
import { ConversationDetail } from "@/components/customer/conversations";
import { RequirePermission } from "@/components/layout/require-permission";

export const metadata: Metadata = { title: "Conversation" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RequirePermission perm="conversation.read"><ConversationDetail id={decodeURIComponent(id)} /></RequirePermission>;
}
