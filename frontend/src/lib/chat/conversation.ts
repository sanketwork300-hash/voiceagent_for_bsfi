import { ACTION_TOOLS, INTENT_STATUS, toolLabel } from "@/config/tools";
import type { AgentResponse, AuthState, ConversationMessage, FailureOutcome, Intent, PendingActionView, RiskLevel, SourceCitation } from "@/types/domain";
import { AUTH_ORDER } from "@/types/domain";
import type { BackendFrame } from "@/types/events";

export interface ToolStep {
  id: string;
  tool: string;
  status: "running" | "completed" | "failed" | "held"; // held = paused by policy (auth / confirmation / approval)
  latencyMs: number | null;
  source?: string;
  riskLevel?: RiskLevel;
  outcome?: FailureOutcome;
  policyDecision?: string | null;
  error?: string | null;
  result?: unknown; // already masked by the backend gateway; rendered only in operator views
}

export type Slip =
  | { kind: "verify"; id: string; step: "identify" | "otp"; purpose?: "login" | "transaction"; destination?: string; tool?: string;
      requiredAuth: AuthState | null; summary?: string; details?: Record<string, unknown>; state: "open" | "done" | "closed" }
  | { kind: "confirm"; id: string; actionId: string; tool: string; summary: string; details?: Record<string, unknown>; riskLevel: RiskLevel;
      expiresAt: string; state: "open" | "confirmed" | "cancelled" | "superseded" }
  | { kind: "approval"; id: string; approvalId: string; tool: string; summary?: string; details?: Record<string, unknown>; reason: string;
      state: "pending" | "closed" }
  | { kind: "receipt"; id: string; tool: string; result: Record<string, unknown>; latencyMs: number | null }
  | { kind: "failure"; id: string; tool: string; outcome: FailureOutcome; error: string | null; policyDecision: string | null };

export interface ChatMessage {
  id: string;
  role: "customer" | "agent" | "human" | "system";
  text: string;
  at: string;
  status: "thinking" | "streaming" | "done" | "interrupted" | "error";
  tools: ToolStep[];
  sources: SourceCitation[];
  slips: Slip[];
  intent: Intent | null;
  response?: AgentResponse;
  statusLabel?: string | null;
  masked?: boolean; // customer message that was a secret (OTP) — never shown
  reference?: string;
}

export interface ConversationState {
  messages: ChatMessage[];
  phase: "idle" | "thinking" | "streaming";
  authState: AuthState;
  language: string;
  languageTag: string;
  intent: Intent | null;
  pendingAction: PendingActionView | null;
  handoff: { status: "none" | "requested" | "connected" | "resolved"; id?: string; reason?: string; priority?: string };
}

export const initialConversation = (authState: AuthState = "UNAUTHENTICATED"): ConversationState => ({
  messages: [], phase: "idle", authState, language: "en", languageTag: "en", intent: null, pendingAction: null, handoff: { status: "none" },
});

export type ConversationAction =
  | { type: "user.sent"; id: string; text: string; at: string; masked?: boolean }
  | { type: "frame"; frame: BackendFrame; at: string }
  | { type: "history"; messages: ConversationMessage[] }
  | { type: "local.error"; message: string; reference: string | null }
  | { type: "reset"; authState?: AuthState };

let seq = 0;
const nid = (p: string) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

function updateLastAgent(state: ConversationState, fn: (m: ChatMessage) => ChatMessage): ConversationState {
  const idx = [...state.messages].reverse().findIndex((m) => m.role === "agent");
  if (idx < 0) return state;
  const i = state.messages.length - 1 - idx;
  const messages = state.messages.slice();
  messages[i] = fn(messages[i]);
  return { ...state, messages };
}

function ensureAgent(state: ConversationState, at: string): ConversationState {
  const last = state.messages[state.messages.length - 1];
  if (last && last.role === "agent" && (last.status === "thinking" || last.status === "streaming")) return state;
  return { ...state, messages: [...state.messages, emptyAgent(at)] };
}

const emptyAgent = (at: string): ChatMessage => ({
  id: nid("a"), role: "agent", text: "", at, status: "thinking", tools: [], sources: [], slips: [], intent: null, statusLabel: "Understanding your request",
});

/** Close slips from earlier turns once the backend no longer reports them pending. */
function settleSlips(messages: ChatMessage[], response: AgentResponse, currentId: string): ChatMessage[] {
  const executed = new Set(response.tool_calls.filter((t) => t.status === "completed").map((t) => t.name));
  return messages.map((m) => {
    if (m.id === currentId) return m;
    let changed = false;
    const slips = m.slips.map((s): Slip => {
      if (s.kind === "confirm" && s.state === "open" && response.pending_action?.id !== s.actionId) {
        changed = true;
        return { ...s, state: executed.has(s.tool) ? "confirmed" : response.pending_action?.tool === s.tool ? "superseded" : "cancelled" };
      }
      if (s.kind === "verify" && s.state === "open" && !(response.pending_action?.decision === "REQUIRE_AUTH")) {
        changed = true;
        return { ...s, state: s.requiredAuth && AUTH_ORDER.indexOf(response.authentication_state) >= AUTH_ORDER.indexOf(s.requiredAuth) ? "done" : "closed" };
      }
      if (s.kind === "approval" && s.state === "pending" && response.pending_action?.decision !== "REQUIRE_HUMAN_APPROVAL") {
        changed = true;
        return { ...s, state: "closed" };
      }
      return s;
    });
    return changed ? { ...m, slips } : m;
  });
}

export function conversationReducer(state: ConversationState, action: ConversationAction): ConversationState {
  switch (action.type) {
    case "reset":
      return initialConversation(action.authState);
    case "history":
      return {
        ...state,
        messages: action.messages.filter((m) => ["user", "assistant", "human_agent"].includes(m.role)).map((m) => ({
          id: m.id, role: m.role === "user" ? "customer" : m.role === "assistant" ? "agent" : "human",
          text: m.content, at: m.created_at, status: m.interrupted ? "interrupted" : "done",
          tools: m.tool_calls.map((t) => ({ id: t.id, tool: t.name, status: t.status === "completed" ? "completed" : "failed", latencyMs: t.latency_ms,
                                            policyDecision: t.decision })),
          sources: m.sources, slips: [], intent: m.intent, masked: m.content === "[OTP REDACTED]",
        })),
      };
    case "user.sent":
      return {
        ...state, phase: "thinking",
        messages: [...state.messages,
          { id: action.id, role: "customer", text: action.text, at: action.at, status: "done", tools: [], sources: [], slips: [], intent: null, masked: action.masked },
          emptyAgent(action.at)],
      };
    case "local.error":
      return {
        ...updateLastAgent(state, (m) => (m.status === "thinking" || m.status === "streaming"
          ? { ...m, status: "error", statusLabel: null, text: m.text || action.message,
              slips: m.slips, response: m.response, ...(action.reference ? { reference: action.reference } : {}) }
          : m)),
        phase: "idle",
      };
    case "frame":
      return applyFrame(state, action.frame, action.at);
  }
}

function applyFrame(state: ConversationState, f: BackendFrame, at: string): ConversationState {
  switch (f.type) {
    case "processing.started":
      return { ...ensureAgent(state, at), phase: "thinking" };
    case "intent.detected": {
      const s = updateLastAgent(ensureAgent(state, at), (m) => ({ ...m, intent: f.data.intent as Intent, statusLabel: INTENT_STATUS[f.data.intent] ?? m.statusLabel }));
      return { ...s, intent: f.data.intent as Intent, language: f.data.language, languageTag: f.data.language_tag ?? f.data.language };
    }
    case "tool.started":
      return updateLastAgent(ensureAgent(state, at), (m) => ({
        ...m, statusLabel: toolLabel(f.tool),
        tools: [...m.tools, { id: nid("t"), tool: f.tool, status: "running", latencyMs: null }],
      }));
    case "tool.completed":
      return updateLastAgent(state, (m) => {
        const tools = markTool(m.tools, f.tool, { status: "completed", latencyMs: f.data.latency_ms, source: f.data.source, riskLevel: f.data.risk_level, result: f.data.result });
        const slips = ACTION_TOOLS[f.tool] && f.data.result && typeof f.data.result === "object"
          ? [...m.slips, { kind: "receipt" as const, id: nid("s"), tool: f.tool, result: f.data.result as Record<string, unknown>, latencyMs: f.data.latency_ms }]
          : m.slips;
        return { ...m, tools, slips };
      });
    case "tool.failed":
      return updateLastAgent(state, (m) => {
        const outcome = f.data.outcome ?? "unknown";
        const tools = markTool(m.tools, f.tool, { status: "failed", latencyMs: f.data.latency_ms ?? null, outcome, error: f.data.error,
                                                  policyDecision: f.data.policy_decision, source: f.data.source });
        const slips = ACTION_TOOLS[f.tool]
          ? [...m.slips, { kind: "failure" as const, id: nid("s"), tool: f.tool, outcome, error: f.data.error, policyDecision: f.data.policy_decision }]
          : m.slips;
        return { ...m, tools, slips };
      });
    case "knowledge.sources":
      return updateLastAgent(state, (m) => ({ ...m, sources: f.data.sources }));
    case "auth.required":
      return updateLastAgent(ensureAgent(state, at), (m) => ({
        ...m, tools: f.tool ? markTool(m.tools, f.tool, { status: "held", policyDecision: "REQUIRE_AUTH" }) : m.tools, slips: [...m.slips, { kind: "verify", id: nid("s"), step: f.data.step, purpose: f.data.purpose, destination: f.data.destination,
          tool: f.tool, requiredAuth: f.data.required_auth_state, summary: f.data.summary, details: f.data.details, state: "open" }],
      }));
    case "confirmation.required":
      return updateLastAgent(ensureAgent(state, at), (m) => ({
        ...m, tools: markTool(m.tools, f.tool, { status: "held", policyDecision: "REQUIRE_CONFIRMATION" }), slips: [...m.slips, { kind: "confirm", id: nid("s"), actionId: f.data.action_id, tool: f.tool, summary: f.data.summary,
          details: f.data.details, riskLevel: f.data.risk_level, expiresAt: f.data.expires_at, state: "open" }],
      }));
    case "approval.required":
      return updateLastAgent(ensureAgent(state, at), (m) => ({
        ...m, tools: markTool(m.tools, f.tool, { status: "held", policyDecision: "REQUIRE_HUMAN_APPROVAL" }), slips: [...m.slips, { kind: "approval", id: nid("s"), approvalId: f.data.approval_id, tool: f.tool, summary: f.data.summary,
          details: f.data.details, reason: f.data.reason, state: "pending" }],
      }));
    case "handoff.initiated":
      return { ...state, handoff: { status: "requested", id: f.data.handoff_id, reason: f.data.reason, priority: f.data.priority } };
    case "handoff.accepted":
      return { ...state, handoff: { ...state.handoff, status: "connected" } };
    case "handoff.resolved":
      return { ...state, handoff: { ...state.handoff, status: "resolved" } };
    case "human.message":
      return { ...state, messages: [...state.messages, { id: nid("h"), role: "human", text: f.content, at, status: "done", tools: [], sources: [], slips: [], intent: null }] };
    case "message.delta":
      return { ...updateLastAgent(ensureAgent(state, at), (m) => ({ ...m, status: "streaming", text: m.text + f.content })), phase: "streaming" };
    case "message.completed": {
      const r = f.response;
      let s = updateLastAgent(ensureAgent(state, at), (m) => ({
        ...m, status: r.error ? "error" : r.interrupted ? "interrupted" : "done", text: r.text || m.text, statusLabel: null,
        tools: m.tools.map((t) => (t.status === "running" ? { ...t, status: r.tool_calls.find((c) => c.name === t.tool)?.status === "pending" ? "held" : "completed" } : t)),
        sources: r.sources.length ? r.sources : m.sources, intent: r.intent ?? m.intent, response: r,
      }));
      const current = s.messages[s.messages.length - 1];
      s = { ...s, messages: settleSlips(s.messages, r, current.id) };
      return {
        ...s, phase: "idle", authState: r.authentication_state, pendingAction: r.pending_action, intent: r.intent ?? s.intent,
        language: r.language ?? s.language,
        handoff: r.handoff && s.handoff.status === "none" ? { status: "requested", id: r.handoff_id ?? undefined } : s.handoff,
      };
    }
    case "message.interrupted":
      return { ...updateLastAgent(state, (m) => (m.status === "streaming" || m.status === "thinking" ? { ...m, status: "interrupted", statusLabel: null } : m)), phase: "idle" };
    case "error":
      return { ...updateLastAgent(state, (m) => (m.status === "done" ? m : { ...m, status: "error", statusLabel: null,
        text: m.text || "We couldn't complete that just now. Nothing was changed." })), phase: "idle" };
    default:
      return state;
  }
}

function markTool(tools: ToolStep[], tool: string, patch: Partial<ToolStep>): ToolStep[] {
  const idx = tools.map((t) => t.tool === tool && t.status === "running").lastIndexOf(true);
  if (idx < 0) return [...tools, { id: nid("t"), tool, status: "running", latencyMs: null, ...patch } as ToolStep];
  const out = tools.slice();
  out[idx] = { ...out[idx], ...patch };
  return out;
}

/** Synthesise frames from a non-streaming REST response (fallback when the WebSocket is unavailable). */
export function framesFromResponse(r: AgentResponse): BackendFrame[] {
  const frames: BackendFrame[] = [];
  for (const t of r.tool_calls) {
    frames.push({ type: "tool.started", tool: t.name });
    if (t.status === "completed") frames.push({ type: "tool.completed", tool: t.name, data: { latency_ms: t.latency_ms } });
    else if (t.status !== "pending") frames.push({ type: "tool.failed", tool: t.name, data: { error: null, policy_decision: t.decision } });
  }
  if (r.sources.length) frames.push({ type: "knowledge.sources", data: { sources: r.sources } });
  const pa = r.pending_action;
  if (pa?.decision === "REQUIRE_CONFIRMATION") frames.push({ type: "confirmation.required", tool: pa.tool, data: { action_id: pa.id, summary: pa.summary, risk_level: "HIGH", expires_at: pa.expires_at } });
  if (pa?.decision === "REQUIRE_AUTH") frames.push({ type: "auth.required", tool: pa.tool, data: { step: "otp", required_auth_state: pa.required_auth_state, summary: pa.summary } });
  if (pa?.decision === "REQUIRE_HUMAN_APPROVAL") frames.push({ type: "approval.required", tool: pa.tool, data: { approval_id: "", reason: pa.summary, summary: pa.summary } });
  frames.push({ type: "message.completed", response: r });
  return frames;
}

export function authLevel(a: AuthState): number {
  return AUTH_ORDER.indexOf(a);
}
