from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.database.models import APITool, MCPTool
from app.domain import AuthState, Channel, RiskLevel, new_id
from app.policies.approval import ActionGrant, action_hash
from app.tools.schemas import ToolCallRequest, ToolContext

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("")
async def list_tools(p: StaffPrincipal = Depends(require(Permission.AGENT_READ)), c=Depends(container)) -> list[dict]:
    """The normalised tool catalogue — what the LLM sees, independent of REST/OpenAPI/MCP backing."""
    tools = await c.registry.tools_for(p.tenant_id)
    return [t.llm_view() | {"source": t.source.value, "min_auth_state": t.min_auth_state.value, "enabled": t.enabled,
                            "internal": t.internal, "requires_confirmation": t.requires_confirmation, "id": t.binding.get("tool_id")}
            for t in tools.values()]


class ToolGovernance(BaseModel):
    risk_level: RiskLevel | None = None
    min_auth_state: AuthState | None = None
    requires_confirmation: bool | None = None
    is_enabled: bool | None = None
    confirmation_template: str | None = None


async def _row(c, tenant_id: str, tool_id: str):
    async with c.db.session() as s:
        for model in (APITool, MCPTool):
            row = (await s.execute(select(model).where(model.tenant_id == tenant_id,
                                                       (model.id == tool_id) | (model.name == tool_id)))).scalar_one_or_none()
            if row:
                return model, row
    raise HTTPException(404, "tool not found")


@router.patch("/{tool_id}")
async def update_governance(tool_id: str, body: ToolGovernance, p: StaffPrincipal = Depends(require(Permission.POLICY_MANAGE)),
                            c=Depends(container)) -> dict:
    model, row = await _row(c, p.tenant_id, tool_id)
    changes = {k: (v.value if hasattr(v, "value") else v) for k, v in body.model_dump(exclude_none=True).items()}
    async with c.db.session() as s:
        db_row = await s.get(model, row.id)
        for k, v in changes.items():
            setattr(db_row, k, v)
    c.registry.invalidate(p.tenant_id)
    await c.audit.record(p.tenant_id, "tool.governance_updated", actor_type="staff", actor_id=p.user_id, resource=row.name, payload=changes)
    return {"tool": row.name, "updated": changes}


class ToolTest(BaseModel):
    arguments: dict[str, Any] = {}
    customer_id: str | None = None
    auth_state: AuthState = AuthState.FULLY_AUTHENTICATED
    simulate_confirmation: bool = False  # sandbox only


@router.post("/{tool_id}/test")
async def test_tool(tool_id: str, body: ToolTest, p: StaffPrincipal = Depends(require(Permission.TOOL_TEST)), c=Depends(container)) -> dict:
    """Run a tool through the real gateway + policy engine with a synthetic customer context."""
    _, row = await _row(c, p.tenant_id, tool_id) if tool_id not in ("search_knowledge", "request_human_handoff") else (None, None)
    name = row.name if row else tool_id
    tools = await c.registry.tools_for(p.tenant_id)
    tool = tools.get(name)
    if tool is None:
        raise HTTPException(404, "tool not found")
    if body.simulate_confirmation and c.settings.is_production:
        raise HTTPException(403, "simulate_confirmation is disabled in production")
    ctx = ToolContext(tenant_id=p.tenant_id, session_id=f"tooltest-{new_id()}", conversation_id="tooltest", customer_id=body.customer_id,
                      channel=Channel.CHAT, auth_state=body.auth_state, auth_methods=["otp"],
                      txn_auth_action_hash=action_hash(name, body.arguments) if body.auth_state == AuthState.TRANSACTION_AUTHENTICATED else None)
    grant = ActionGrant(action_hash=action_hash(name, body.arguments), confirmed=True) if body.simulate_confirmation else None
    out = await c.gateway.execute(ToolCallRequest(id="test", name=name, arguments=body.arguments), ctx, offered={name: tool}, grant=grant)
    await c.audit.record(p.tenant_id, "tool.tested", actor_type="staff", actor_id=p.user_id, resource=name,
                         payload={"decision": out.decision.decision.value if out.decision else None})
    return {"tool": tool.llm_view(), "decision": out.decision.model_dump() if out.decision else None,
            "result": out.result.model_dump()}
