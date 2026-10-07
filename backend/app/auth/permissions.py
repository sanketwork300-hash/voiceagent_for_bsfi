"""Staff RBAC: roles -> permissions."""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    TENANT_ADMIN = "tenant:admin"
    TENANT_READ = "tenant:read"
    USER_MANAGE = "user:manage"
    AGENT_MANAGE = "agent:manage"
    AGENT_READ = "agent:read"
    KNOWLEDGE_MANAGE = "knowledge:manage"
    KNOWLEDGE_READ = "knowledge:read"
    INTEGRATION_MANAGE = "integration:manage"
    TOOL_TEST = "tool:test"
    POLICY_MANAGE = "policy:manage"
    AUDIT_READ = "audit:read"
    CONVERSATION_READ = "conversation:read"
    HANDOFF_HANDLE = "handoff:handle"
    APPROVAL_DECIDE = "approval:decide"
    SESSION_CREATE = "session:create"
    EVALUATION_RUN = "evaluation:run"


ROLE_PERMISSIONS: dict[str, set[Permission]] = {
    "platform_admin": set(Permission),
    "tenant_admin": set(Permission) - {Permission.APPROVAL_DECIDE},  # maker-checker: admins don't approve money movement
    "developer": {Permission.AGENT_READ, Permission.KNOWLEDGE_READ, Permission.INTEGRATION_MANAGE, Permission.TOOL_TEST,
                  Permission.SESSION_CREATE, Permission.EVALUATION_RUN, Permission.TENANT_READ},
    "knowledge_manager": {Permission.KNOWLEDGE_MANAGE, Permission.KNOWLEDGE_READ, Permission.TENANT_READ},
    "supervisor": {Permission.CONVERSATION_READ, Permission.HANDOFF_HANDLE, Permission.APPROVAL_DECIDE,
                   Permission.AGENT_READ, Permission.TENANT_READ},
    "human_agent": {Permission.CONVERSATION_READ, Permission.HANDOFF_HANDLE, Permission.TENANT_READ},
    "auditor": {Permission.AUDIT_READ, Permission.CONVERSATION_READ, Permission.TENANT_READ},
    "channel_service": {Permission.SESSION_CREATE, Permission.AGENT_READ},  # e.g. the bank's web/app backend
}


def permissions_for(roles: list[str]) -> set[Permission]:
    out: set[Permission] = set()
    for r in roles:
        out |= ROLE_PERMISSIONS.get(r, set())
    return out
