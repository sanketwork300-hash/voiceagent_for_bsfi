"use client";
import { useMemo } from "react";
import { conversationReducer, initialConversation } from "@/lib/chat/conversation";
import type { ConversationMessage } from "@/types/domain";
import { MessageView } from "./message";

const noop = () => undefined;
const NO_ACTIONS = { confirm: noop, cancel: noop, otp: noop, identify: noop, checkApproval: noop, retry: noop, support: noop };

/** Read-only transcript of a stored conversation (chat and voice turns, in order). */
export function TranscriptView({ messages }: { messages: ConversationMessage[] }) {
  const state = useMemo(() => conversationReducer(initialConversation(), { type: "history", messages }), [messages]);
  const channelOf = new Map(messages.map((m) => [m.id, m.channel]));
  return (
    <div className="grid gap-6">
      {state.messages.map((m) => (
        <div key={m.id} className="grid gap-1">
          <span className="text-meta text-subtle">{channelOf.get(m.id) === "voice" ? "Voice" : "Chat"}</span>
          <MessageView m={m} audience="operator" slipActions={NO_ACTIONS} busy={false} />
        </div>
      ))}
    </div>
  );
}
