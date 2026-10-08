import { describe, expect, it } from "vitest";
import { conversationReducer, framesFromResponse, initialConversation, type ConversationState } from "@/lib/chat/conversation";
import type { AgentResponse } from "@/types/domain";
import type { BackendFrame } from "@/types/events";

const at = "2026-10-07T10:00:00Z";
const resp = (p: Partial<AgentResponse> = {}): AgentResponse => ({
  text: "", intent: "ACTION_REQUEST", language: "en", tool_calls: [], sources: [], handoff: false, handoff_id: null, pending_action: null,
  authentication_state: "FULLY_AUTHENTICATED", session_id: "s", conversation_id: "c", message_id: "m", interrupted: false, error: null, ...p,
});
const apply = (s: ConversationState, ...frames: BackendFrame[]) => frames.reduce((acc, frame) => conversationReducer(acc, { type: "frame", frame, at }), s);
const send = (s: ConversationState, text: string) => conversationReducer(s, { type: "user.sent", id: text, text, at });
const lastAgent = (s: ConversationState) => [...s.messages].reverse().find((m) => m.role === "agent")!;

describe("conversation reducer", () => {
  it("streams deltas, tool steps and sources into one assistant message", () => {
    let s = send(initialConversation(), "What is my loan balance?");
    s = apply(s, { type: "processing.started" }, { type: "intent.detected", data: { intent: "CUSTOMER_DATA_QUERY", confidence: 0.9, language: "en", carried_over: false } },
      { type: "tool.started", tool: "get_loan_details" });
    expect(lastAgent(s).statusLabel).toBe("Checking your loan");
    s = apply(s, { type: "tool.completed", tool: "get_loan_details", data: { latency_ms: 12, source: "openapi" } }, { type: "message.delta", content: "Your " }, { type: "message.delta", content: "loan" });
    expect(lastAgent(s).text).toBe("Your loan");
    expect(s.phase).toBe("streaming");
    s = apply(s, { type: "message.completed", response: resp({ text: "Your loan is ₹28,45,000.", intent: "CUSTOMER_DATA_QUERY" }) });
    const m = lastAgent(s);
    expect(m.status).toBe("done");
    expect(m.tools).toEqual([expect.objectContaining({ tool: "get_loan_details", status: "completed", latencyMs: 12 })]);
    expect(s.phase).toBe("idle");
  });

  it("transfer: OTP slip → confirm slip → receipt; earlier slips settle", () => {
    let s = send(initialConversation("FULLY_AUTHENTICATED"), "Transfer ₹1,00,000 to Rahul");
    s = apply(s, { type: "tool.started", tool: "transfer_money" },
      { type: "auth.required", tool: "transfer_money", data: { step: "otp", purpose: "transaction", destination: "XXXXXX3210", required_auth_state: "TRANSACTION_AUTHENTICATED", summary: "transfer ₹1,00,000 to Rahul", details: { amount: 100000, payee_name: "Rahul" } } },
      { type: "message.completed", response: resp({ pending_action: { id: "pa1", tool: "transfer_money", decision: "REQUIRE_AUTH", summary: "x", required_auth_state: "TRANSACTION_AUTHENTICATED", expires_at: at } }) });
    expect(lastAgent(s).tools[0].status).toBe("held");
    expect(lastAgent(s).slips[0]).toMatchObject({ kind: "verify", state: "open", purpose: "transaction" });
    s = conversationReducer(s, { type: "user.sent", id: "otp", text: "One-time password entered", at, masked: true });
    s = apply(s, { type: "tool.started", tool: "transfer_money" },
      { type: "confirmation.required", tool: "transfer_money", data: { action_id: "pa2", summary: "transfer ₹1,00,000 to Rahul", risk_level: "CRITICAL", details: { amount: 100000 }, expires_at: at } },
      { type: "message.completed", response: resp({ authentication_state: "TRANSACTION_AUTHENTICATED", pending_action: { id: "pa2", tool: "transfer_money", decision: "REQUIRE_CONFIRMATION", summary: "x", required_auth_state: null, expires_at: at } }) });
    expect(s.messages.find((m) => m.slips.some((x) => x.kind === "verify"))!.slips[0]).toMatchObject({ state: "done" });
    s = send(s, "yes");
    s = apply(s, { type: "tool.started", tool: "transfer_money" },
      { type: "tool.completed", tool: "transfer_money", data: { latency_ms: 20, result: { status: "SUCCESS", transaction_ref: "IMPS123", amount: 100000 } } },
      { type: "message.completed", response: resp({ tool_calls: [{ id: "1", name: "transfer_money", status: "completed", decision: "ALLOW", latency_ms: 20, arguments: {} }] }) });
    expect(lastAgent(s).slips[0]).toMatchObject({ kind: "receipt", result: expect.objectContaining({ transaction_ref: "IMPS123" }) });
    expect(s.messages.flatMap((m) => m.slips).find((x) => x.kind === "confirm")).toMatchObject({ state: "confirmed" });
    expect(s.messages.find((m) => m.masked)?.text).toBe("One-time password entered");
  });

  it("cancelling closes the confirmation as cancelled", () => {
    let s = apply(send(initialConversation(), "Block my card"),
      { type: "confirmation.required", tool: "block_card", data: { action_id: "a", summary: "block", risk_level: "HIGH", expires_at: at } },
      { type: "message.completed", response: resp({ pending_action: { id: "a", tool: "block_card", decision: "REQUIRE_CONFIRMATION", summary: "", required_auth_state: null, expires_at: at } }) });
    s = apply(send(s, "no"), { type: "message.completed", response: resp({ text: "Cancelled." }) });
    expect(s.messages.flatMap((m) => m.slips)[0]).toMatchObject({ state: "cancelled" });
  });

  it("action failures keep the institution outcome so the UI never guesses", () => {
    const s = apply(send(initialConversation(), "transfer"), { type: "tool.started", tool: "transfer_money" },
      { type: "tool.failed", tool: "transfer_money", data: { error: "timeout", policy_decision: null, outcome: "unknown" } });
    expect(lastAgent(s).slips[0]).toMatchObject({ kind: "failure", outcome: "unknown" });
    expect(lastAgent(s).tools[0]).toMatchObject({ status: "failed", outcome: "unknown" });
  });

  it("handoff, human messages and resolution", () => {
    let s = apply(send(initialConversation(), "human please"), { type: "handoff.initiated", data: { handoff_id: "h", reason: "CUSTOMER_REQUEST", priority: "normal" } });
    expect(s.handoff.status).toBe("requested");
    s = apply(s, { type: "handoff.accepted" }, { type: "human.message", content: "Hi, I'm Asha." });
    expect(s.handoff.status).toBe("connected");
    expect(s.messages.at(-1)).toMatchObject({ role: "human", text: "Hi, I'm Asha." });
    expect(apply(s, { type: "handoff.resolved", returned_to_agent: true }).handoff.status).toBe("resolved");
  });

  it("interrupt marks the streaming message interrupted", () => {
    const s = apply(send(initialConversation(), "x"), { type: "message.delta", content: "Par" }, { type: "message.interrupted" });
    expect(lastAgent(s).status).toBe("interrupted");
  });

  it("hydrates history, masking redacted OTP messages", () => {
    const s = conversationReducer(initialConversation(), { type: "history", messages: [
      { id: "1", role: "user", channel: "voice", content: "[OTP REDACTED]", language: "en", intent: null, tool_calls: [], sources: [], interrupted: false, created_at: at },
      { id: "2", role: "assistant", channel: "voice", content: "Verified", language: "en", intent: null, tool_calls: [], sources: [], interrupted: false, created_at: at },
      { id: "3", role: "tool", channel: "chat", content: "{}", language: null, intent: null, tool_calls: [], sources: [], interrupted: false, created_at: at },
    ] });
    expect(s.messages).toHaveLength(2);
    expect(s.messages[0].masked).toBe(true);
  });

  it("REST fallback frames reproduce the same state", () => {
    const r = resp({ text: "ok", tool_calls: [{ id: "1", name: "get_card_status", status: "completed", decision: "ALLOW", latency_ms: 5, arguments: {} }] });
    const s = apply(send(initialConversation(), "card"), ...framesFromResponse(r));
    expect(lastAgent(s)).toMatchObject({ status: "done", text: "ok" });
    expect(lastAgent(s).tools[0].status).toBe("completed");
  });
});
