"""Deterministic, server-side workflow templates.

A template turns one model-proposed action into a fixed DAG: resolve/validate inputs first, then the action, then a
read-only verification of what really happened. Templates are platform configuration — the LLM cannot add, remove or
reorder their steps, and parameters they bind (e.g. `beneficiary_id`) are hidden from the model and stripped if it
tries to supply them.

A template only applies when every tool it needs is registered for the tenant; otherwise the action runs as a single
policy-gated step exactly as before (backward compatible).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.execution.models import ExecutionStep, StepKind, StepOrigin
from app.tools.schemas import OperationType, SideEffect, ToolDefinition

CheckFn = Callable[[ExecutionStep, dict[str, ExecutionStep]], tuple[bool, dict[str, Any], str | None]]


@dataclass(frozen=True)
class VerifySpec:
    tool: str
    args: Callable[[ExecutionStep], dict[str, Any]]
    evaluate: str  # name of the evaluator in app.agents.execution.verifier


@dataclass(frozen=True)
class WorkflowTemplate:
    name: str
    action_tool: str
    helper_tools: tuple[str, ...] = ()
    bound_params: tuple[str, ...] = ()
    verify: VerifySpec | None = None
    expand_before: Callable[[ExecutionStep, Callable[[], str]], list[ExecutionStep]] | None = None
    bindings: dict[str, str] = field(default_factory=dict)  # param -> "<helper index>.<output field>"

    def applies(self, tools: dict[str, ToolDefinition]) -> bool:
        # helpers must be registered AND enabled (newly discovered MCP tools start disabled until reviewed)
        return self.action_tool in tools and all(t in tools and tools[t].enabled for t in self.helper_tools)

    def verify_tool(self, tools: dict[str, ToolDefinition]) -> str | None:
        v = self.verify.tool if self.verify else None
        return v if v and v in tools and tools[v].enabled else None


# ---------------------------------------------------------------------------------------- checks (pure functions)
def check_single_beneficiary(step: ExecutionStep, deps: dict[str, ExecutionStep]) -> tuple[bool, dict[str, Any], str | None]:
    """Exactly one *active* registered beneficiary must match; otherwise ask, never guess."""
    lookup = next((d for d in deps.values() if d.tool == "find_beneficiary"), None)
    data = (lookup.result.data if lookup and lookup.result else None) or {}
    matches = [b for b in data.get("beneficiaries", []) if str(b.get("status", "ACTIVE")).upper() == "ACTIVE"]
    if not matches:
        return False, {}, "No registered beneficiary matches that name."
    if len(matches) > 1:
        names = ", ".join(str(b.get("name")) for b in matches[:5])
        return False, {}, f"More than one beneficiary matches ({names}); please say the full name."
    b = matches[0]
    if not b.get("beneficiary_id"):
        return False, {}, "The beneficiary record is incomplete."
    return True, {"beneficiary_id": b["beneficiary_id"], "beneficiary_name": b.get("name"),
                  "account_masked": b.get("account_number_masked")}, None


CHECKS: dict[str, CheckFn] = {"single_beneficiary": check_single_beneficiary}


# ---------------------------------------------------------------------------------------- templates
def _transfer_before(action: ExecutionStep, new_id: Callable[[], str]) -> list[ExecutionStep]:
    find = ExecutionStep(id=new_id(), origin=StepOrigin.TEMPLATE, template="transfer_funds", tool="find_beneficiary",
                         arguments={"name": str(action.arguments.get("payee_name", ""))},
                         operation_type=OperationType.READ, side_effect=SideEffect.ACCOUNT_READ, parallel_safe=True)
    check = ExecutionStep(id=new_id(), kind=StepKind.CHECK, origin=StepOrigin.TEMPLATE, template="transfer_funds",
                          check="single_beneficiary", depends_on=[find.id],
                          operation_type=OperationType.READ, side_effect=SideEffect.NONE, parallel_safe=True)
    return [find, check]


TRANSFER_FUNDS = WorkflowTemplate(
    name="transfer_funds", action_tool="transfer_money", helper_tools=("find_beneficiary",),
    bound_params=("beneficiary_id",), bindings={"beneficiary_id": "-1.beneficiary_id"},  # -1: the last helper (the check)
    expand_before=_transfer_before,
    verify=VerifySpec(tool="get_transfer_status", args=lambda s: {"idempotency_key": s.idempotency_key or ""},
                      evaluate="transfer_status"),
)

BLOCK_CARD = WorkflowTemplate(
    name="block_card", action_tool="block_card",
    verify=VerifySpec(tool="get_card_status", args=lambda s: {"card_type": s.arguments.get("card_type")} if s.arguments.get("card_type") else {},
                      evaluate="card_blocked"),
)

TEMPLATES: dict[str, WorkflowTemplate] = {t.action_tool: t for t in (TRANSFER_FUNDS, BLOCK_CARD)}


def template_for(tool: str, tools: dict[str, ToolDefinition]) -> WorkflowTemplate | None:
    t = TEMPLATES.get(tool)
    return t if t and t.applies(tools) else None


def apply_bound_params(tools: dict[str, ToolDefinition]) -> None:
    """Registry hook: template-bound parameters are never model input — hidden from the schema and stripped by the
    gateway — even when the template cannot run (then the action simply executes without them)."""
    for t in TEMPLATES.values():
        if t.bound_params and t.action_tool in tools:
            tool = tools[t.action_tool]
            tool.bound_params = sorted(set(tool.bound_params) | set(t.bound_params))
