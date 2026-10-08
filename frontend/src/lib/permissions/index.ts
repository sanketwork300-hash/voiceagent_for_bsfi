/**
 * UI permissions. These only decide what to render; the backend authorises every request regardless.
 * Backend roles/permissions: backend/app/auth/permissions.py — keep the mapping in sync.
 */
export type BackendPermission =
  | "tenant:admin" | "tenant:read" | "user:manage" | "agent:manage" | "agent:read" | "knowledge:manage" | "knowledge:read"
  | "integration:manage" | "tool:test" | "policy:manage" | "audit:read" | "conversation:read" | "handoff:handle"
  | "approval:decide" | "session:create" | "evaluation:run";

export type Permission =
  | "customer.read" | "customer.update" | "conversation.read" | "tool.execute" | "tool.configure" | "integration.manage"
  | "mcp.manage" | "policy.manage" | "approval.execute" | "audit.read" | "knowledge.read" | "knowledge.manage"
  | "agent.read" | "agent.manage" | "user.manage" | "handoff.handle" | "evaluation.run" | "tenant.read" | "tenant.admin";

const MAP: Record<Permission, BackendPermission> = {
  "customer.read": "conversation:read",
  "customer.update": "tenant:admin",
  "conversation.read": "conversation:read",
  "tool.execute": "tool:test",
  "tool.configure": "policy:manage",
  "integration.manage": "integration:manage",
  "mcp.manage": "integration:manage",
  "policy.manage": "policy:manage",
  "approval.execute": "approval:decide",
  "audit.read": "audit:read",
  "knowledge.read": "knowledge:read",
  "knowledge.manage": "knowledge:manage",
  "agent.read": "agent:read",
  "agent.manage": "agent:manage",
  "user.manage": "user:manage",
  "handoff.handle": "handoff:handle",
  "evaluation.run": "evaluation:run",
  "tenant.read": "tenant:read",
  "tenant.admin": "tenant:admin",
};

const ALL: BackendPermission[] = [
  "tenant:admin", "tenant:read", "user:manage", "agent:manage", "agent:read", "knowledge:manage", "knowledge:read",
  "integration:manage", "tool:test", "policy:manage", "audit:read", "conversation:read", "handoff:handle",
  "approval:decide", "session:create", "evaluation:run",
];

export const ROLE_PERMISSIONS: Record<string, BackendPermission[]> = {
  platform_admin: ALL,
  tenant_admin: ALL.filter((p) => p !== "approval:decide"), // maker-checker
  developer: ["agent:read", "knowledge:read", "integration:manage", "tool:test", "session:create", "evaluation:run", "tenant:read"],
  knowledge_manager: ["knowledge:manage", "knowledge:read", "tenant:read"],
  supervisor: ["conversation:read", "handoff:handle", "approval:decide", "agent:read", "tenant:read"],
  human_agent: ["conversation:read", "handoff:handle", "tenant:read"],
  auditor: ["audit:read", "conversation:read", "tenant:read"],
  channel_service: ["session:create", "agent:read"],
};

export const ROLE_LABELS: Record<string, string> = {
  platform_admin: "Platform admin", tenant_admin: "Administrator", developer: "Developer", knowledge_manager: "Knowledge manager",
  supervisor: "Supervisor", human_agent: "Human agent", auditor: "Auditor", channel_service: "Channel service",
};

export function backendPermissions(roles: string[]): Set<BackendPermission> {
  return new Set(roles.flatMap((r) => ROLE_PERMISSIONS[r] ?? []));
}

export function can(roles: string[] | undefined, perm: Permission): boolean {
  if (!roles) return false;
  return backendPermissions(roles).has(MAP[perm]);
}

export function primaryRole(roles: string[]): string {
  const order = ["platform_admin", "tenant_admin", "supervisor", "developer", "knowledge_manager", "auditor", "human_agent", "channel_service"];
  return order.find((r) => roles.includes(r)) ?? roles[0] ?? "";
}
