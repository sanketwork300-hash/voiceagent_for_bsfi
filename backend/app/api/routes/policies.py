from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.database.models import Policy
from app.domain import new_id
from app.escalation.handoff import session_events_channel
from app.policies.rules import DEFAULT_RULES, RULE_EFFECTS, PolicyRule

router = APIRouter(tags=["policies"])


class PolicyCreate(BaseModel):
    id: str | None = Field(None, description="Use a default rule's id to override/disable it")
    name: str
    description: str = ""
    priority: int = 100
    conditions: dict[str, Any] = {}
    effect: str
    params: dict[str, Any] = {}
    is_enabled: bool = True


@router.post("/policies", status_code=201)
async def create_policy(body: PolicyCreate, p: StaffPrincipal = Depends(require(Permission.POLICY_MANAGE)), c=Depends(container)) -> dict:
    if body.effect not in RULE_EFFECTS:
        raise HTTPException(400, f"effect must be one of {sorted(RULE_EFFECTS)}")
    try:
        PolicyRule(id=body.id or "x", name=body.name, conditions=body.conditions, effect=body.effect, params=body.params)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    async with c.db.session() as s:
        row = await s.get(Policy, body.id) if body.id else None
        if row and row.tenant_id != p.tenant_id:
            raise HTTPException(409, "policy id in use")
        if row is None:
            row = Policy(id=body.id or new_id(), tenant_id=p.tenant_id, name=body.name, effect=body.effect)
            s.add(row)
        for k, v in body.model_dump(exclude={"id"}).items():
            setattr(row, k, v)
    c.policy.invalidate(p.tenant_id)
    await c.audit.record(p.tenant_id, "policy.upserted", actor_type="staff", actor_id=p.user_id, resource=row.id, payload=body.model_dump())
    return {"id": row.id, "name": row.name, "effect": row.effect, "is_enabled": row.is_enabled}


@router.get("/policies")
async def list_policies(p: StaffPrincipal = Depends(require(Permission.POLICY_MANAGE)), c=Depends(container)) -> dict:
    effective = await c.policy.rules_for(p.tenant_id)
    async with c.db.session() as s:
        disabled = (await s.execute(select(Policy).where(Policy.tenant_id == p.tenant_id, Policy.is_enabled.is_(False)))).scalars().all()
    return {"effective": [r.model_dump() for r in effective], "defaults": [r.id for r in DEFAULT_RULES],
            "disabled": [{"id": r.id, "name": r.name, "priority": r.priority, "conditions": r.conditions, "effect": r.effect,
                          "params": r.params, "enabled": False} for r in disabled]}


@router.get("/approvals")
async def pending_approvals(p: StaffPrincipal = Depends(require(Permission.APPROVAL_DECIDE)), c=Depends(container)) -> list[dict]:
    return [{"id": a.id, "session_id": a.session_id, "tool": a.tool_name, "arguments": a.arguments, "risk_level": a.risk_level,
             "reason": a.reason, "created_at": a.created_at} for a in await c.approvals.list_pending(p.tenant_id)]


class Decision(BaseModel):
    approve: bool


@router.post("/approvals/{approval_id}/decision")
async def decide(approval_id: str, body: Decision, p: StaffPrincipal = Depends(require(Permission.APPROVAL_DECIDE)), c=Depends(container)) -> dict:
    req = await c.approvals.decide(tenant_id=p.tenant_id, approval_id=approval_id, approve=body.approve, staff_user_id=p.user_id)
    if req is None:
        raise HTTPException(404, "approval not found")
    await c.audit.record(p.tenant_id, "approval.decided", actor_type="staff", actor_id=p.user_id, resource=approval_id,
                         session_id=req.session_id, payload={"status": req.status, "tool": req.tool_name})
    await c.store.publish(session_events_channel(req.session_id), {"type": "approval.decided", "status": req.status})
    return {"id": req.id, "status": req.status}
