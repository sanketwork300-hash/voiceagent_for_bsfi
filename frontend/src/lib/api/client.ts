import type {
  ActivityItem, Agent, ApprovalRequest, AuditEvent, ConversationMessage, ConversationSummary, CustomerSession,
  EvaluationReport, Handoff, Integration, KnowledgeDocument, McpServer, McpTool, MonitoringSummary, PolicyRule,
  SessionView, SourceCitation, StaffProfile, StaffUser, Tenant, ToolView, AgentResponse, Channel, AuthState, RiskLevel,
} from "@/types/domain";
import { request } from "./http";
import * as customersMock from "./mock/customers";
import * as platformMock from "./mock/platform";

/** Same-origin Next.js route handlers (cookie-based staff session, demo identity provider). */
async function local<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const { ApiError, messageForStatus } = await import("./errors");
    throw new ApiError(res.status, (data as { message?: string }).message ?? messageForStatus(res.status), res.headers.get("x-request-id"));
  }
  return data as T;
}

export const apiClient = {
  auth: {
    login: (tenant: string, email: string, password: string) =>
      local<{ mfa_required: boolean; profile?: StaffProfile }>("/api/auth/login", { tenant, email, password }),
    verifyMfa: (code: string) => local<{ profile: StaffProfile }>("/api/auth/mfa", { code }),
    me: () => local<{ profile: StaffProfile | null }>("/api/auth/me"),
    logout: () => local<{ ok: true }>("/api/auth/logout", {}),
    deviceSessions: platformMock.listDeviceSessions,
  },
  tenants: { me: () => request<Tenant>("/tenants/me") },
  users: {
    list: () => request<StaffUser[]>("/users"),
    create: (body: { email: string; full_name: string; password: string; roles: string[] }) => request<StaffUser>("/users", { body }),
  },
  agents: {
    list: () => request<Agent[]>("/agents"),
    get: (id: string) => request<Agent>(`/agents/${id}`),
    create: (body: Partial<Agent>) => request<Agent>("/agents", { body }),
    update: (id: string, body: Partial<Agent> & { clear_tool_restriction?: boolean; llm_config?: Record<string, unknown> }) =>
      request<Agent>(`/agents/${id}`, { method: "PATCH", body }),
  },
  sessions: {
    /** Anonymous customer session (no bank login yet). */
    create: (tenant: string, channel: Channel = "chat") =>
      request<CustomerSession>("/sessions", { body: { tenant, channel } }),
    /** Demo only: the Next server signs a customer assertion as the bank's identity provider would. */
    createDemoAuthenticated: (customerId = "CUST1001") =>
      local<CustomerSession>("/api/demo/customer-session", { customer_id: customerId }),
    get: (id: string, token: string) => request<SessionView>(`/sessions/${id}`, { sessionToken: token }),
    messages: (id: string, token: string) => request<ConversationMessage[]>(`/sessions/${id}/messages`, { sessionToken: token }),
    identify: (id: string, token: string, phone: string) =>
      request<{ success: boolean; message: string; session: SessionView }>(`/sessions/${id}/auth/identify`, { body: { phone }, sessionToken: token }),
    close: (id: string, token: string) => request<{ closed: boolean }>(`/sessions/${id}/close`, { body: {}, sessionToken: token }),
  },
  chat: {
    send: (sessionId: string, token: string, message: string) =>
      request<AgentResponse>("/chat/message", { body: { session_id: sessionId, message }, sessionToken: token }),
  },
  voice: {
    start: (token: string) =>
      request<{ session: SessionView; room: string; livekit_url: string; participant_token: string }>("/voice/session", { body: {}, sessionToken: token }),
    token: (token: string) => request<{ room: string; livekit_url: string; participant_token: string }>("/voice/token", { body: {}, sessionToken: token }),
    simulate: (token: string, transcript: string) =>
      request<{ response: AgentResponse | null; speech_text: string | null }>("/voice/simulate", { body: { transcript }, sessionToken: token }),
  },
  handoff: {
    request: (sessionId: string, token: string, note?: string) =>
      request<{ handoff_id: string; status: string; priority: string }>("/handoff", { body: { session_id: sessionId, reason: "CUSTOMER_REQUEST", note }, sessionToken: token }),
    queue: (status = "queued") => request<Handoff[]>("/handoff/queue", { query: { status } }),
    accept: (id: string) => request<Handoff>(`/handoff/${id}/accept`, { body: {} }),
    reply: (id: string, text: string) => request<{ sent: boolean }>(`/handoff/${id}/reply`, { body: { text } }),
    resolve: (id: string, resolution: string, returnToAgent: boolean) =>
      request<{ id: string; status: string }>(`/handoff/${id}/resolve`, { body: { resolution, return_to_agent: returnToAgent } }),
  },
  conversations: {
    list: (limit = 100) => request<ConversationSummary[]>("/conversations", { query: { limit } }),
    messages: (id: string) => request<ConversationMessage[]>(`/conversations/${id}/messages`),
  },
  customers: { source: customersMock.CUSTOMERS_SOURCE, list: customersMock.listCustomers, get: customersMock.getCustomer },
  documents: {
    list: () => request<KnowledgeDocument[]>("/documents"),
    upload: (form: FormData) => request<{ document_id: string; version_id: string; version: string; status: string; chunks: number; error: string | null }>("/documents", { form }),
    reindex: (id: string) => request<{ status: string; chunks: number }>(`/documents/${id}/reindex`, { body: {} }),
  },
  knowledge: {
    search: (query: string, opts: { product?: string; doc_type?: string; top_k?: number; include_internal?: boolean } = {}) =>
      request<{ results: SourceCitation[] }>("/knowledge/search", { body: { query, ...opts } }),
  },
  integrations: {
    list: () => request<Integration[]>("/integrations"),
    create: (body: { name: string; kind: string; base_url?: string; auth_type: string; credentials?: Record<string, string>; config?: Record<string, unknown> }) =>
      request<{ id: string; name: string; kind: string; status: string }>("/integrations", { body }),
    test: (id: string) => request<{ ok: boolean; detail: string; latency_ms: number }>(`/integrations/${id}/test`, { body: {} }),
    importOpenApi: (id: string, body: { spec?: unknown; only?: string[]; enable?: boolean }) =>
      request<{ imported: (Pick<ToolView, "name" | "description" | "input_schema" | "risk_level" | "min_auth_state" | "internal">)[] }>(`/integrations/${id}/import-openapi`, { body }),
  },
  mcp: {
    list: () => request<McpServer[]>("/mcp/servers"),
    tools: (id: string) => request<McpTool[]>(`/mcp/servers/${id}/tools`),
    discover: (id: string) => request<McpTool[]>(`/mcp/servers/${id}/discover`, { body: {} }),
    register: (body: { name: string; url: string; integration_id?: string; auto_enable?: boolean }) =>
      request<{ id: string; name: string; tools: McpTool[] }>("/mcp/servers", { body }),
  },
  tools: {
    list: () => request<ToolView[]>("/tools"),
    update: (name: string, body: Partial<{ risk_level: RiskLevel; min_auth_state: AuthState; requires_confirmation: boolean; is_enabled: boolean }>) =>
      request<{ tool: string; updated: Record<string, unknown> }>(`/tools/${name}`, { method: "PATCH", body }),
    test: (name: string, body: { arguments: Record<string, unknown>; customer_id?: string; auth_state?: AuthState }) =>
      request<{ tool: ToolView; decision: { decision: string; reason: string; risk_level: RiskLevel; risk_score: number; risk_factors: string[]; matched_rules: string[] } | null;
        result: { ok: boolean; data: unknown; error: string | null; latency_ms: number | null; failure_kind?: string | null } }>(`/tools/${name}/test`, { body }),
  },
  policies: {
    list: () => request<{ effective: PolicyRule[]; defaults: string[]; disabled: PolicyRule[] }>("/policies"),
    upsert: (body: Omit<PolicyRule, "enabled"> & { is_enabled: boolean; description?: string }) => request<{ id: string }>("/policies", { body }),
  },
  approvals: {
    list: () => request<ApprovalRequest[]>("/approvals"),
    decide: (id: string, approve: boolean) => request<{ id: string; status: string }>(`/approvals/${id}/decision`, { body: { approve } }),
  },
  audit: {
    events: (q: { event_type?: string; session_id?: string; limit?: number } = {}) => request<AuditEvent[]>("/audit/events", { query: q }),
    verify: () => request<{ intact: boolean; first_broken_seq: number | null }>("/audit/verify"),
  },
  monitoring: {
    summary: (hours = 24) => request<MonitoringSummary>("/monitoring/summary", { query: { hours } }),
    activity: (limit = 30) => request<ActivityItem[]>("/monitoring/activity", { query: { limit } }),
    ready: () => request<{ status: string; checks: Record<string, boolean> }>("/ready"),
  },
  evaluation: {
    run: (body: { channels?: Channel[]; categories?: string[] } = {}) => request<EvaluationReport>("/evaluation/runs", { body }),
    get: (id: string) => request<EvaluationReport>(`/evaluation/runs/${id}`),
  },
  workflows: { source: "mock" as const, list: platformMock.listWorkflows },
};

export type ApiClient = typeof apiClient;
