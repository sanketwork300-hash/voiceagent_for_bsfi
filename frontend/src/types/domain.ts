// Domain types mirroring the backend contracts (app/domain.py, route responses). Keep in sync with the API.

export type Channel = "chat" | "voice";
export type AuthState =
  | "UNAUTHENTICATED"
  | "IDENTIFIED"
  | "PARTIALLY_AUTHENTICATED"
  | "FULLY_AUTHENTICATED"
  | "TRANSACTION_AUTHENTICATED";
export const AUTH_ORDER: AuthState[] = [
  "UNAUTHENTICATED",
  "IDENTIFIED",
  "PARTIALLY_AUTHENTICATED",
  "FULLY_AUTHENTICATED",
  "TRANSACTION_AUTHENTICATED",
];
export type Intent =
  | "KNOWLEDGE_QUERY"
  | "CUSTOMER_DATA_QUERY"
  | "ACTION_REQUEST"
  | "FRAUD_REQUEST"
  | "GENERAL_CONVERSATION"
  | "HUMAN_HANDOFF";
export type RiskLevel = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type PolicyDecision = "ALLOW" | "DENY" | "REQUIRE_AUTH" | "REQUIRE_CONFIRMATION" | "REQUIRE_HUMAN_APPROVAL";
export type FailureOutcome = "not_executed" | "rejected" | "unknown";

export interface ToolCallSummary {
  id: string;
  name: string;
  status: "completed" | "failed" | "denied" | "pending";
  decision: PolicyDecision | null;
  latency_ms: number | null;
  arguments: Record<string, unknown>;
}

export interface SourceCitation {
  index: number;
  document_id: string;
  title: string;
  version: string | null;
  page: number | null;
  section: string | null;
  chunk_id: string;
  score: number;
  snippet: string;
}

export interface PendingActionView {
  id: string;
  tool: string;
  decision: PolicyDecision;
  summary: string;
  required_auth_state: AuthState | null;
  expires_at: string;
}

export interface AgentResponse {
  text: string;
  intent: Intent | null;
  language: string;
  tool_calls: ToolCallSummary[];
  sources: SourceCitation[];
  handoff: boolean;
  handoff_id: string | null;
  pending_action: PendingActionView | null;
  authentication_state: AuthState;
  session_id: string;
  conversation_id: string | null;
  message_id: string | null;
  interrupted: boolean;
  error: string | null;
}

export interface SessionView {
  session_id: string;
  conversation_id: string;
  channel: Channel;
  active_channels: Channel[];
  language: string;
  authentication_state: AuthState;
  current_intent: Intent | null;
  pending_action: PendingActionView | null;
  status: "ACTIVE" | "HANDED_OFF" | "CLOSED";
  handoff_id: string | null;
}

export interface CustomerSession {
  session: SessionView;
  session_token: string;
  expires_in: number;
}

export interface StaffProfile {
  user_id: string;
  email: string;
  tenant_id: string;
  tenant_slug: string;
  roles: string[];
  expires_at: number; // epoch seconds
}

export interface Tenant {
  id: string;
  slug: string;
  name: string;
  institution_type: string;
  default_language: string;
  supported_languages: string[];
  is_active: boolean;
}

export interface Agent {
  id: string;
  tenant_id: string;
  name: string;
  description: string;
  persona_prompt: string;
  allowed_tools: string[] | null;
  channels: Channel[];
  languages: string[];
  voice_config: Record<string, unknown>;
  llm_config?: Record<string, unknown>;
  is_active: boolean;
}

export interface ConversationSummary {
  id: string;
  channel: Channel;
  channels_used: Channel[];
  language: string;
  status: string;
  last_intent: Intent | null;
  customer_ref: string | null;
  created_at: string;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | "human_agent" | "tool" | "system";
  channel: Channel;
  content: string;
  language: string | null;
  intent: Intent | null;
  tool_calls: ToolCallSummary[];
  sources: SourceCitation[];
  interrupted: boolean;
  created_at: string;
}

export interface DocumentVersion {
  id: string;
  version: string;
  status: "pending" | "indexed" | "failed" | "superseded";
  chunks: number;
  effective_from: string | null;
  effective_until: string | null;
  error: string | null;
  created_at: string;
  filename: string;
}
export interface KnowledgeDocument {
  id: string;
  title: string;
  doc_type: string;
  product: string | null;
  language: string;
  access_level: "public" | "customer" | "internal";
  status: string;
  current_version_id: string | null;
  versions: DocumentVersion[];
}

export interface Integration {
  id: string;
  name: string;
  kind: "rest" | "openapi" | "mcp" | "adapter";
  base_url: string | null;
  auth_type: string;
  status: "healthy" | "degraded" | "down" | "unknown";
  last_checked_at: string | null;
  is_enabled: boolean;
}

export interface ToolView {
  id: string | null;
  name: string;
  description: string;
  input_schema: { type: string; properties?: Record<string, { type?: string; description?: string; enum?: string[] }>; required?: string[] };
  risk_level: RiskLevel;
  source: "builtin" | "rest" | "openapi" | "mcp" | "adapter";
  min_auth_state: AuthState;
  enabled: boolean;
  internal: boolean;
  requires_confirmation: boolean;
  integration_id: string | null;
  server_id: string | null;
  timeout_seconds: number;
  idempotent: boolean;
  injected_params: string[];
}

export interface McpServer {
  id: string;
  name: string;
  transport: string;
  url: string | null;
  integration_id: string | null;
  auth_type: string;
  status: string;
  protocol_version: string | null;
  server_info: Record<string, string>;
  tool_count: number;
  last_discovered_at: string | null;
  is_enabled: boolean;
}

export interface McpTool {
  id: string;
  name: string;
  remote_name: string;
  description: string;
  input_schema: ToolView["input_schema"];
  risk_level: RiskLevel;
  min_auth_state: AuthState;
  requires_confirmation: boolean;
  injected_params: Record<string, string>;
  is_enabled: boolean;
  annotations: Record<string, unknown>;
}

export type RuleEffect = "DENY" | "REQUIRE_AUTH" | "REQUIRE_CONFIRMATION" | "REQUIRE_HUMAN_APPROVAL" | "SET_MIN_AUTH";
export type ComparisonOp = "gt" | "gte" | "lt" | "lte" | "eq" | "ne" | "in";
export interface PolicyRule {
  id: string;
  name: string;
  priority: number;
  conditions: {
    tool?: string | string[];
    intent?: string | string[];
    channel?: string | string[];
    source?: string | string[];
    risk_level_gte?: RiskLevel;
    auth_state_lt?: AuthState;
    args?: Record<string, Partial<Record<ComparisonOp, unknown>>>;
    session?: Record<string, Partial<Record<ComparisonOp, unknown>>>;
    args_plus_session?: { arg: string; stat: string } & Partial<Record<ComparisonOp, number>>;
  };
  effect: RuleEffect;
  params: Record<string, unknown>;
  enabled: boolean;
}

export interface ApprovalRequest {
  id: string;
  session_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  risk_level: RiskLevel;
  reason: string;
  created_at: string;
}

export interface HandoffContext {
  conversation_id: string;
  customer_id: string | null;
  intent: Intent | null;
  authentication_status: AuthState;
  summary: string;
  tools_called: { tool: string; status: string; decision: string | null }[];
  actions_taken: { tool: string; result: Record<string, unknown> }[];
  reason: string;
  note: string | null;
  channel: Channel;
  language: string;
}
export interface Handoff {
  id: string;
  session_id: string;
  conversation_id: string;
  channel: Channel;
  reason: string;
  priority: "normal" | "high" | "urgent";
  status: "queued" | "assigned" | "resolved" | "returned";
  context: HandoffContext;
  created_at: string;
}

export interface AuditEvent {
  seq: number;
  occurred_at: string;
  event_type: string;
  actor_type: string;
  actor_id: string | null;
  session_id: string | null;
  channel: Channel | null;
  resource: string | null;
  outcome: string;
  payload: Record<string, unknown>;
  trace_id: string | null;
  hash: string;
}

export interface MonitoringSummary {
  window_hours: number;
  active_sessions: { total: number; chat: number; voice: number };
  conversations: { total: number; chat: number; voice: number; escalated: number; resolved_without_escalation: number };
  handoffs: { total: number; by_reason: Record<string, number>; rate: number | null };
  tools: {
    total: number; completed: number; failed: number; denied: number; pending: number; success_rate: number | null;
    latency_ms: { avg: number | null; p95: number | null };
    by_tool: { tool: string; source: string; calls: number; failures: number; denied: number; p95_ms: number | null }[];
  };
  policy_decisions: Record<string, number>;
  authentication: { otp_verified: number; otp_failed: number; assertions: number; success_rate: number | null };
  intents: Record<string, number>;
  knowledge_grounding: number | null;
  last_evaluation: { id: string; pass_rate: number; at: string } | null;
  series: { t: string; calls: number; failures: number; latency_ms: number | null }[];
  voice_media: null | Record<string, number>;
}

export interface ActivityItem {
  id: string;
  kind: "tool" | "handoff" | "auth";
  at: string;
  title: string;
  status: string;
  channel: Channel | null;
  latency_ms?: number | null;
  session_id: string | null;
  detail: string | null;
}

export interface EvaluationTurnResult {
  scenario: string;
  channel: Channel;
  turn: number;
  passed: boolean;
  checks: Record<string, boolean>;
  failures: string[];
  latency_ms: number;
  response: string;
}
export interface EvaluationReport {
  id?: string;
  pass_rate: number;
  metrics: Record<string, { turns: number; scenarios: number; scenario_pass_rate: number; turn_pass_rate: number;
    accuracy: Record<string, number>; latency_ms: { p50: number; p95: number } }>;
  results: EvaluationTurnResult[];
}

export interface StaffUser { id: string; email: string; full_name: string; roles: string[]; is_active: boolean }
