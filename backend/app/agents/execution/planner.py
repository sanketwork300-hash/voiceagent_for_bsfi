"""Reason + Plan: turn the model's proposed tool calls into a validated execution DAG.

The model proposes; this module decides the shape of execution, using only trusted server-side metadata:

* identical calls are de-duplicated (a duplicated transfer is executed at most once)
* invalid arguments are not expanded into workflows (the gateway rejects them as a single step)
* workflow templates add resolution / validation / verification steps around an action
* edges:  data edges (`depends_on`)  template wiring and tools' declared `depends_on`
          ordering edges (`after`)   every mutation runs after all earlier steps of its batch and after the previous
                                     mutation; steps after a mutation run after it. So a mutation never overlaps
                                     with any other step, and two mutations are never concurrently schedulable.
* plan policy: at most `max_mutations_per_plan` state-changing actions per request (default 1), a step budget,
  and cycle detection. Rejected steps stay in the plan with status REJECTED so the model gets an explicit answer.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import jsonschema

from app.agents.execution.models import (
    ExecutionStep,
    ReasoningResult,
    StepKind,
    StepOrigin,
    StepStatus,
    WorkflowState,
)
from app.agents.execution.templates import template_for
from app.domain import Intent, RiskLevel
from app.llm.base import LLMToolCall
from app.observability import metrics
from app.tools.schemas import OperationType, SideEffect, ToolDefinition

log = logging.getLogger(__name__)
_INTENT_RISK = {Intent.ACTION_REQUEST: RiskLevel.HIGH, Intent.FRAUD_REQUEST: RiskLevel.HIGH,
                Intent.CUSTOMER_DATA_QUERY: RiskLevel.MEDIUM}


class PlanError(RuntimeError):
    pass


def _canonical(name: str, args: dict[str, Any]) -> str:
    return json.dumps([name, args], sort_keys=True, default=str, separators=(",", ":"))


def apply_tool_metadata(step: ExecutionStep, tool: ToolDefinition) -> None:
    ex = tool.exec
    step.operation_type, step.side_effect = ex.operation_type, ex.side_effect
    step.parallel_safe, step.idempotent = ex.parallel_safe and not ex.mutates, ex.idempotent
    step.concurrency_group, step.max_concurrency = ex.concurrency_group, ex.max_concurrency
    step.timeout_seconds = tool.timeout_seconds
    step.declared_depends_on = list(ex.depends_on)
    if ex.mutates:  # belt and braces: whatever was declared, a mutation is a serialized WRITE
        step.operation_type, step.parallel_safe = OperationType.WRITE, False


class ExecutionPlanner:
    def __init__(self, *, max_steps: int = 12, max_mutations: int = 1) -> None:
        self.max_steps = max_steps
        self.max_mutations = max_mutations

    # ------------------------------------------------------------------------------------------- REASON
    def reason(self, *, intent: Intent, entities: dict[str, Any], offered: dict[str, ToolDefinition]) -> ReasoningResult:
        """Deterministic, user-safe summary of what the request needs (no model chain-of-thought is stored)."""
        constraints = []
        if any(t.exec.mutates for t in offered.values()):
            constraints += [f"max_{self.max_mutations}_state_change_per_request", "mutations_run_alone"]
        if any(t.exec.side_effect == SideEffect.FINANCIAL_MUTATION for t in offered.values()):
            constraints += ["financial_mutations_sequential", "verify_before_reporting_success"]
        required = []
        if intent == Intent.ACTION_REQUEST and ("amount" in entities or "payee_name" in entities):
            required = [k for k in ("amount", "payee_name") if not entities.get(k)]
        safe_entities = {k: v for k, v in entities.items() if k in ("amount", "payee_name", "card_type", "product", "payment_ref")}
        return ReasoningResult(
            reasoning_summary=f"{intent.value.replace('_', ' ').lower()}"
                              + (f" involving {', '.join(sorted(safe_entities))}" if safe_entities else ""),
            intent=intent, entities=safe_entities, constraints=constraints, required_information=required,
            risk=_INTENT_RISK.get(intent, RiskLevel.LOW))

    # ------------------------------------------------------------------------------------------- PLAN
    def extend(self, wf: WorkflowState, calls: list[LLMToolCall], *, offered: dict[str, ToolDefinition],
               all_tools: dict[str, ToolDefinition]) -> tuple[list[ExecutionStep], dict[str, str]]:
        """Append the steps for one model response to the workflow. Returns (new steps, duplicate call_id -> step id)."""
        seen = {_canonical(s.tool or "", s.arguments): s for s in wf.steps if s.origin == StepOrigin.LLM and s.tool}
        aliases: dict[str, str] = {}
        batch: list[ExecutionStep] = []
        counter = [len(wf.steps)]

        def next_id() -> str:
            counter[0] += 1
            return f"s{counter[0]}"

        mutations_so_far = sum(1 for s in wf.steps if s.origin == StepOrigin.LLM and s.mutates and s.status != StepStatus.REJECTED)
        budget = self.max_steps - sum(1 for s in wf.steps if s.status != StepStatus.REJECTED)
        for call in calls:
            key = _canonical(call.name, call.arguments)
            if key in seen:
                aliases[call.id] = seen[key].id
                metrics.duplicate_prevented.labels("duplicate_call_financial" if seen[key].financial else "duplicate_call").inc()
                continue
            step = ExecutionStep(id=next_id(), tool=call.name, call_id=call.id, arguments=dict(call.arguments))
            seen[key] = step
            tool = offered.get(call.name)
            group = [step]
            if tool is not None:
                apply_tool_metadata(step, tool)
                if step.mutates:
                    if mutations_so_far >= self.max_mutations:
                        self._reject(step, f"Only {self.max_mutations} account change can be made per request; "
                                           "please ask for the next one separately.", "extra_mutation")
                        batch.append(step)
                        continue
                    mutations_so_far += 1
                if self._schema_valid(tool, step.arguments) and (tpl := template_for(call.name, all_tools)) is not None:
                    group = self._expand(step, tpl, all_tools, next_id)
            # unknown / not-offered tools stay inert single steps: the gateway rejects (and audits) them
            if len(group) > budget:
                self._reject(step, "This request has too many steps; please split it up.", "step_budget")
                batch.append(step)
                continue
            budget -= len(group)
            batch.extend(group)

        self._order(wf.steps, batch)
        wf.steps.extend(batch)
        try:
            self._check_acyclic(wf)
            self._assert_mutations_serialized(wf)
        except PlanError as e:  # fail closed: nothing from this batch runs
            log.error("rejecting unsafe plan", extra={"workflow_id": wf.workflow_id, "error": str(e)})
            for st in batch:
                st.depends_on, st.after = [], []
                if st.status != StepStatus.REJECTED:
                    self._reject(st, "This request could not be planned safely.", "unsafe_plan")
        return batch, aliases

    @staticmethod
    def _reject(step: ExecutionStep, reason: str, metric_reason: str) -> None:
        step.status, step.status_reason = StepStatus.REJECTED, reason
        metrics.policy_blocks.labels("plan", metric_reason).inc()
        if step.mutates:
            metrics.duplicate_prevented.labels(metric_reason).inc()

    @staticmethod
    def _schema_valid(tool: ToolDefinition, args: dict[str, Any]) -> bool:
        clean = {k: v for k, v in args.items() if k not in tool.injected_params and k not in tool.bound_params}
        try:
            jsonschema.validate(clean, tool.llm_schema())
            return True
        except jsonschema.ValidationError:
            return False

    @staticmethod
    def _expand(step: ExecutionStep, tpl, all_tools: dict[str, ToolDefinition], next_id) -> list[ExecutionStep]:
        before: list[ExecutionStep] = tpl.expand_before(step, next_id) if tpl.expand_before else []
        for h in before:
            if h.tool and h.tool in all_tools:
                apply_tool_metadata(h, all_tools[h.tool])
        step.template = tpl.name
        if before:
            step.depends_on.append(before[-1].id)
            for param, ref in tpl.bindings.items():
                idx, field = ref.split(".", 1)
                step.bindings[param] = f"{before[int(idx)].id}.{field}"
        group = [*before, step]
        if tpl.verify_tool(all_tools):
            v = ExecutionStep(id=next_id(), kind=StepKind.VERIFY, origin=StepOrigin.TEMPLATE, template=tpl.name,
                              tool=tpl.verify.tool, verifies=step.id, depends_on=[step.id],
                              operation_type=OperationType.VERIFY, side_effect=SideEffect.ACCOUNT_READ, parallel_safe=True)
            group.append(v)
        return group

    @staticmethod
    def _order(existing: list[ExecutionStep], batch: list[ExecutionStep]) -> None:
        """Ordering + declared-dependency edges for a new batch (see module docstring)."""
        live = [s for s in batch if s.status != StepStatus.REJECTED]
        prev_mutation = next((s for s in reversed(existing) if s.mutates and s.kind == StepKind.TOOL
                              and s.status not in (StepStatus.REJECTED,)), None)
        by_tool: dict[str, list[ExecutionStep]] = {}
        for s in [*existing, *live]:
            if s.tool:
                by_tool.setdefault(s.tool, []).append(s)
        for i, s in enumerate(live):
            earlier = live[:i]
            if s.mutates and s.kind == StepKind.TOOL:
                s.after = sorted({*s.after, *(e.id for e in earlier if e.id not in s.depends_on and e.verifies is None)})
                if prev_mutation is not None:
                    s.after = sorted({*s.after, prev_mutation.id})
                prev_mutation = s
            elif prev_mutation is not None and s.verifies != prev_mutation.id and prev_mutation.id not in s.depends_on:
                # anything proposed after a mutation observes its effect: run it afterwards
                s.after = sorted({*s.after, prev_mutation.id})
        # declared depends_on (trusted tool registration, by tool name) -> data edge to the latest such step
        for s in live:
            for dep_tool in s.declared_depends_on:
                targets = [t for t in by_tool.get(dep_tool, []) if t.id != s.id]
                if targets:
                    s.depends_on = sorted({*s.depends_on, targets[-1].id})

    @staticmethod
    def _check_acyclic(wf: WorkflowState) -> None:
        ids = {s.id: s for s in wf.steps}
        state: dict[str, int] = {}

        def visit(sid: str, path: list[str]) -> None:
            if state.get(sid) == 2:
                return
            if state.get(sid) == 1:
                raise PlanError(f"dependency cycle: {' -> '.join([*path, sid])}")
            state[sid] = 1
            node = ids.get(sid)
            for d in (*(node.depends_on if node else ()), *(node.after if node else ())):
                if d not in ids:
                    raise PlanError(f"unknown dependency {d}")
                visit(d, [*path, sid])
            state[sid] = 2

        for sid in ids:
            visit(sid, [])

    @staticmethod
    def _assert_mutations_serialized(wf: WorkflowState) -> None:
        """Invariant: every pair of live mutations is ordered (one is an ancestor of the other)."""
        ids = {s.id: s for s in wf.steps}

        def ancestors(sid: str, acc: set[str]) -> set[str]:
            for d in (*ids[sid].depends_on, *ids[sid].after):
                if d not in acc:
                    acc.add(d)
                    ancestors(d, acc)
            return acc

        muts = [s for s in wf.steps if s.mutates and s.kind == StepKind.TOOL and s.status != StepStatus.REJECTED]
        for i, a in enumerate(muts):
            for b in muts[i + 1:]:
                if a.id not in ancestors(b.id, set()) and b.id not in ancestors(a.id, set()):
                    raise PlanError(f"mutations {a.id} and {b.id} could run concurrently")

