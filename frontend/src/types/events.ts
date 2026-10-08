// Backend runtime events (WebSocket frames) and the frontend's canonical event model.
import type { AgentResponse, AuthState, FailureOutcome, RiskLevel, SourceCitation } from "./domain";

export type BackendFrame =
  | { type: "session.ready"; session: import("./domain").SessionView }
  | { type: "typing"; state: "started" | "stopped" }
  | { type: "processing.started"; data?: { request_id?: string } }
  | { type: "intent.detected"; data: { intent: string; confidence: number; language: string; language_tag?: string; carried_over: boolean } }
  | { type: "message.delta"; content: string }
  | { type: "tool.started"; tool: string }
  | { type: "tool.completed"; tool: string; data: { latency_ms: number | null; source?: string; risk_level?: RiskLevel; result?: unknown } }
  | { type: "tool.failed"; tool: string; data: { error: string | null; policy_decision: string | null; outcome?: FailureOutcome; latency_ms?: number | null; source?: string } }
  | { type: "knowledge.sources"; data: { sources: SourceCitation[] } }
  | { type: "auth.required"; tool?: string; data: AuthRequiredData }
  | { type: "confirmation.required"; tool: string; data: ConfirmationData }
  | { type: "approval.required"; tool: string; data: ApprovalData }
  | { type: "handoff.initiated"; data: { handoff_id: string; reason: string; priority: string } }
  | { type: "message.completed"; response: AgentResponse }
  | { type: "message.interrupted" }
  | { type: "human.message"; content: string }
  | { type: "handoff.accepted" }
  | { type: "handoff.resolved"; returned_to_agent: boolean }
  | { type: "approval.decided"; status: string }
  | { type: "error"; content?: string }
  | { type: "pong" };

export interface AuthRequiredData {
  step: "identify" | "otp";
  purpose?: "login" | "transaction";
  destination?: string;
  required_auth_state: AuthState | null;
  action_id?: string;
  summary?: string;
  details?: Record<string, unknown>;
  risk_level?: RiskLevel;
}
export interface ConfirmationData {
  action_id: string;
  summary: string;
  risk_level: RiskLevel;
  details?: Record<string, unknown>;
  expires_at: string;
}
export interface ApprovalData {
  approval_id: string;
  reason: string;
  summary?: string;
  details?: Record<string, unknown>;
  risk_level?: RiskLevel;
}

/** Canonical names used by the UI (spec §47). */
export type CanonicalEventType =
  | "session.started" | "message.created" | "message.delta" | "message.completed"
  | "tool.started" | "tool.progress" | "tool.completed" | "tool.failed"
  | "knowledge.search.started" | "knowledge.search.completed"
  | "authentication.required" | "authentication.completed"
  | "confirmation.required" | "approval.required"
  | "handoff.started" | "handoff.completed"
  | "voice.started" | "voice.listening" | "voice.speaking" | "voice.interrupted" | "voice.ended";
