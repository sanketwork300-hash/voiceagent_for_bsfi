"""Unit tests for the execution engine: planning (DAG), plan policy, scheduling, concurrency, failures, hashing."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agents.execution.concurrency import ConcurrencyLimitTimeout, ToolConcurrencyManager
from app.agents.execution.executor import PlanExecutor, apply_verification
from app.agents.execution.models import (
    ExecutionStep,
    StepKind,
    StepStatus,
    VerificationResult,
    VerificationStatus,
    WorkflowState,
)
from app.agents.execution.planner import ExecutionPlanner
from app.domain import AuthState, RiskLevel
from app.i18n.lexicon import confirmation_reply
from app.llm.base import LLMToolCall
from app.policies.approval import action_hash
from app.tools.failures import Disposition, FailureCategory, disposition, may_retry
from app.tools.schemas import (
    ExecutionMetadata,
    OperationType,
    SideEffect,
    ToolDefinition,
    ToolSource,
    resolve_execution,
)


def tool(name, *, write=False, financial=False, depends_on=(), schema=None, group=None):
    ex = None
    if write:
        ex = ExecutionMetadata(operation_type=OperationType.WRITE,
                               side_effect=SideEffect.FINANCIAL_MUTATION if financial else SideEffect.ACCOUNT_MUTATION,
                               depends_on=list(depends_on), concurrency_group=group)
    else:
        ex = ExecutionMetadata(operation_type=OperationType.READ, side_effect=SideEffect.ACCOUNT_READ, parallel_safe=True,
                               idempotent=True, depends_on=list(depends_on), concurrency_group=group)
    return ToolDefinition(name=name, description=name, source=ToolSource.MCP, risk_level=RiskLevel.MEDIUM,
                          min_auth_state=AuthState.FULLY_AUTHENTICATED, execution=ex,
                          input_schema=schema or {"type": "object", "properties": {"x": {"type": "string"}}})


def call(name, cid=None, **args):
    return LLMToolCall(id=cid or f"c_{name}", name=name, arguments=args)


def wf():
    return WorkflowState(tenant_id="t", session_id="s")


# ------------------------------------------------------------------------------------------- trusted metadata
def test_resolve_execution_is_conservative():
    # an integration cannot declare a POST to be a parallel-safe read
    ex = resolve_execution(source=ToolSource.OPENAPI, binding={"method": "POST"}, idempotent=False, internal=False,
                           declared={"operation_type": "READ", "parallel_safe": True})
    assert ex.operation_type == OperationType.WRITE and not ex.parallel_safe and ex.mutates
    # a mutating side effect forces WRITE even on a GET
    ex = resolve_execution(source=ToolSource.OPENAPI, binding={"method": "GET"}, idempotent=True, internal=False,
                           declared={"side_effect": "FINANCIAL_MUTATION", "parallel_safe": True})
    assert ex.operation_type == OperationType.WRITE and not ex.parallel_safe
    # unknown MCP tool without readOnlyHint: mutating, serialized
    ex = resolve_execution(source=ToolSource.MCP, binding={}, idempotent=False, internal=False)
    assert ex.mutates and not ex.parallel_safe
    ex = resolve_execution(source=ToolSource.MCP, binding={}, idempotent=False, internal=False, read_hint=True)
    assert ex.operation_type == OperationType.READ and ex.parallel_safe


def test_llm_never_sees_bound_or_injected_params():
    t = tool("transfer_money", write=True, schema={"type": "object", "properties": {
        "customer_id": {"type": "string"}, "beneficiary_id": {"type": "string"}, "amount": {"type": "number"}},
        "required": ["customer_id", "amount"]})
    t.injected_params = {"customer_id": "customer_id"}
    t.bound_params = ["beneficiary_id"]
    assert set(t.llm_schema()["properties"]) == {"amount"} and t.llm_schema()["required"] == ["amount"]


# ------------------------------------------------------------------------------------------- planning / DAG
def test_independent_reads_form_one_parallel_wave():
    offered = {n: tool(n) for n in ("a", "b", "c")}
    w = wf()
    ExecutionPlanner().extend(w, [call("a"), call("b"), call("c")], offered=offered, all_tools=offered)
    ready, _ = PlanExecutor.schedule(w)
    assert [s.tool for s in ready] == ["a", "b", "c"]


def test_declared_dependencies_chain_a_b_c():
    offered = {"a": tool("a"), "b": tool("b", depends_on=["a"]), "c": tool("c", depends_on=["b"])}
    w = wf()
    ExecutionPlanner().extend(w, [call("c"), call("b"), call("a")], offered=offered, all_tools=offered)
    order = []
    while True:
        ready, _ = PlanExecutor.schedule(w)
        if not ready:
            break
        assert len(ready) == 1
        order.append(ready[0].tool)
        ready[0].status = StepStatus.COMPLETED
    assert order == ["a", "b", "c"]


def test_fan_in_a_b_then_c():
    offered = {"a": tool("a"), "b": tool("b"), "c": tool("c", depends_on=["a", "b"])}
    w = wf()
    ExecutionPlanner().extend(w, [call("a"), call("b"), call("c")], offered=offered, all_tools=offered)
    ready, _ = PlanExecutor.schedule(w)
    assert {s.tool for s in ready} == {"a", "b"}
    for s in ready:
        s.status = StepStatus.COMPLETED
    ready, _ = PlanExecutor.schedule(w)
    assert [s.tool for s in ready] == ["c"]


def test_failed_dependency_skips_dependants():
    offered = {"a": tool("a"), "b": tool("b", depends_on=["a"])}
    w = wf()
    ExecutionPlanner().extend(w, [call("a"), call("b")], offered=offered, all_tools=offered)
    w.steps[0].status = StepStatus.FAILED
    ready, skip = PlanExecutor.schedule(w)
    assert not ready and skip[0][0].tool == "b"


def test_duplicate_calls_are_executed_once():
    offered = {"transfer": tool("transfer", write=True, financial=True)}
    w = wf()
    steps, aliases = ExecutionPlanner().extend(w, [call("transfer", "c1", x="1"), call("transfer", "c2", x="1")],
                                               offered=offered, all_tools=offered)
    assert len(steps) == 1 and aliases == {"c2": steps[0].id}


def test_second_mutation_in_one_request_is_rejected():
    offered = {"t1": tool("t1", write=True, financial=True), "t2": tool("t2", write=True, financial=True), "r": tool("r")}
    w = wf()
    steps, _ = ExecutionPlanner(max_mutations=1).extend(w, [call("t1"), call("t2"), call("r")], offered=offered, all_tools=offered)
    by = {s.tool: s for s in steps}
    assert by["t1"].status == StepStatus.PENDING and by["t2"].status == StepStatus.REJECTED
    # the LLM cannot get around it in a later iteration of the same turn either
    more, _ = ExecutionPlanner(max_mutations=1).extend(w, [call("t2", "c9", x="2")], offered=offered, all_tools=offered)
    assert more[0].status == StepStatus.REJECTED


def test_mutations_never_overlap_even_when_allowed_two_per_plan():
    offered = {"t1": tool("t1", write=True), "t2": tool("t2", write=True), "r1": tool("r1"), "r2": tool("r2")}
    w = wf()
    ExecutionPlanner(max_mutations=2).extend(w, [call("r1"), call("t1"), call("r2"), call("t2")], offered=offered, all_tools=offered)
    waves = []
    while True:
        ready, _ = PlanExecutor.schedule(w)
        if not ready:
            break
        batch = PlanExecutor(gateway=None, concurrency=None, verifier=None, store=None, inflight=None)._wave(ready)  # type: ignore[arg-type]
        waves.append([s.tool for s in batch])
        for s in batch:
            s.status = StepStatus.COMPLETED
    # every wave containing a mutation contains only that mutation; mutations are ordered as proposed
    for wave in waves:
        assert not (any(t.startswith("t") for t in wave) and len(wave) > 1)
    flat = [t for wave in waves for t in wave]
    assert flat.index("r1") < flat.index("t1") < flat.index("r2") < flat.index("t2")


def test_mutation_waits_for_in_flight_or_unverified_mutation():
    offered = {"t1": tool("t1", write=True), "t2": tool("t2", write=True)}
    w = wf()
    ExecutionPlanner(max_mutations=2).extend(w, [call("t1"), call("t2")], offered=offered, all_tools=offered)
    w.steps[0].status = StepStatus.UNKNOWN  # sent, outcome unknown
    ready, skip = PlanExecutor.schedule(w)
    assert not ready and skip and skip[0][0].tool == "t2"  # never proceed past an ambiguous money movement


def test_invalid_arguments_are_not_expanded_into_a_workflow():
    transfer = tool("transfer_money", write=True, financial=True, schema={
        "type": "object", "properties": {"amount": {"type": "number"}, "payee_name": {"type": "string"}}, "required": ["amount"]})
    all_tools = {"transfer_money": transfer, "find_beneficiary": tool("find_beneficiary"), "get_transfer_status": tool("get_transfer_status")}
    w = wf()
    steps, _ = ExecutionPlanner().extend(w, [call("transfer_money", amount="all of it")], offered={"transfer_money": transfer},
                                         all_tools=all_tools)
    assert [s.tool for s in steps] == ["transfer_money"]
    ok, _ = ExecutionPlanner().extend(wf(), [call("transfer_money", "c2", amount=10, payee_name="Rahul")],
                                      offered={"transfer_money": transfer}, all_tools=all_tools)
    assert [s.kind for s in ok] == [StepKind.TOOL, StepKind.CHECK, StepKind.TOOL, StepKind.VERIFY]
    assert ok[2].bindings == {"beneficiary_id": f"{ok[1].id}.beneficiary_id"}


def test_unknown_tools_stay_inert_single_steps():
    w = wf()
    steps, _ = ExecutionPlanner().extend(w, [call("delete_everything")], offered={}, all_tools={})
    assert len(steps) == 1 and not steps[0].mutates  # the gateway rejects (and audits) it; nothing to schedule around


# ------------------------------------------------------------------------------------------- verification
def test_verification_folding_never_reports_unconfirmed_success():
    m = ExecutionStep(id="s1", tool="transfer_money", operation_type=OperationType.WRITE, status=StepStatus.UNKNOWN)
    apply_verification(m, VerificationResult(status=VerificationStatus.SUCCESS, method="status_lookup", evidence={"amount": 1}))
    assert m.status == StepStatus.COMPLETED and m.result and m.result.ok
    m2 = ExecutionStep(id="s2", tool="transfer_money", operation_type=OperationType.WRITE, status=StepStatus.UNKNOWN)
    apply_verification(m2, VerificationResult(status=VerificationStatus.TIMEOUT, method="status_lookup"))
    assert m2.status == StepStatus.UNKNOWN
    m3 = ExecutionStep(id="s3", tool="transfer_money", operation_type=OperationType.WRITE, status=StepStatus.COMPLETED)
    apply_verification(m3, VerificationResult(status=VerificationStatus.FAILED, method="status_lookup"))
    assert m3.status == StepStatus.UNKNOWN and m3.verification.status == VerificationStatus.PARTIAL  # contradiction -> human


# ------------------------------------------------------------------------------------------- failures / retries
def test_retry_policy_by_trusted_metadata():
    read = ExecutionMetadata(operation_type=OperationType.READ, side_effect=SideEffect.ACCOUNT_READ, idempotent=True)
    write = ExecutionMetadata(operation_type=OperationType.WRITE, side_effect=SideEffect.ACCOUNT_MUTATION, idempotent=True)
    fin = ExecutionMetadata(operation_type=OperationType.WRITE, side_effect=SideEffect.FINANCIAL_MUTATION, idempotent=True)
    nonidem = ExecutionMetadata(operation_type=OperationType.WRITE, side_effect=SideEffect.ACCOUNT_MUTATION, idempotent=False)
    assert may_retry(FailureCategory.TIMEOUT, read, sent=None, has_idempotency_key=False)
    assert not may_retry(FailureCategory.NOT_FOUND, read, sent=None, has_idempotency_key=False)
    assert may_retry(FailureCategory.TIMEOUT, write, sent=None, has_idempotency_key=True)
    assert not may_retry(FailureCategory.TIMEOUT, write, sent=None, has_idempotency_key=False)
    assert not may_retry(FailureCategory.TIMEOUT, nonidem, sent=None, has_idempotency_key=True)
    for cat in FailureCategory:  # financial: never, whatever the error
        assert not may_retry(cat, fin, sent=False, has_idempotency_key=True)
    assert disposition(FailureCategory.TIMEOUT, fin) == Disposition.REQUIRES_VERIFICATION
    assert disposition(FailureCategory.PARTIAL_FAILURE, read) == Disposition.REQUIRES_HUMAN
    assert disposition(FailureCategory.VALIDATION_ERROR, fin) == Disposition.NON_RETRYABLE


# ------------------------------------------------------------------------------------------- concurrency limits
async def test_session_and_group_limits_bound_concurrency():
    cm = ToolConcurrencyManager(global_limit=10, group_limits={"bank": 2}, session_limit=10)
    active, peak = 0, 0

    async def one(i):
        nonlocal active, peak
        async with cm.slot(tenant_id="t", session_id=f"s{i}", tool="x", group="bank", tool_limit=None, timeout=5):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1

    await asyncio.gather(*(one(i) for i in range(8)))
    assert peak == 2 and cm.snapshot()["keyed"] == 0  # bounded, and per-key semaphores are cleaned up


async def test_concurrency_slot_times_out_instead_of_hanging():
    cm = ToolConcurrencyManager(global_limit=1, session_limit=1)
    async with cm.slot(tenant_id="t", session_id="s", tool="x", group=None, tool_limit=None, timeout=1):
        t0 = time.monotonic()
        with pytest.raises(ConcurrencyLimitTimeout):
            async with cm.slot(tenant_id="t", session_id="s2", tool="x", group=None, tool_limit=None, timeout=0.05):
                pass
        assert time.monotonic() - t0 < 0.5


# ------------------------------------------------------------------------------------------- confirmation binding
@pytest.mark.parametrize("text,expected", [
    ("yes", "affirm"), ("haan kar do", "affirm"), ("ok go ahead", "affirm"),
    ("Yes, but make it ₹50,000", "amend"), ("yes 50000", "amend"), ("yes, to Rohan instead", "amend"),
    ("no", "negate"), ("yes yes go ahead and do it right now please", "unclear"), ("what is my balance", "other"),
])
def test_confirmation_reply_classification(text, expected):
    assert confirmation_reply(text) == expected


def test_action_hash_binds_customer_session_and_tenant():
    args = {"amount": 1000, "payee_name": "Rahul", "beneficiary_id": "BEN01", "currency": "INR"}
    base = action_hash("transfer_money", args, tenant_id="t", session_id="s", customer_id="C1")
    assert base != action_hash("transfer_money", {**args, "amount": 50000}, tenant_id="t", session_id="s", customer_id="C1")
    assert base != action_hash("transfer_money", {**args, "beneficiary_id": "BEN02"}, tenant_id="t", session_id="s", customer_id="C1")
    assert base != action_hash("transfer_money", args, tenant_id="t", session_id="s2", customer_id="C1")
    assert base != action_hash("transfer_money", args, tenant_id="t", session_id="s", customer_id="C2")
    assert base != action_hash("transfer_money", args, tenant_id="t2", session_id="s", customer_id="C1")


def test_template_requires_enabled_helpers():
    transfer = tool("transfer_money", write=True, financial=True, schema={
        "type": "object", "properties": {"amount": {"type": "number"}, "payee_name": {"type": "string"}}, "required": ["amount"]})
    helper = tool("find_beneficiary").model_copy(update={"enabled": False})  # discovered, not yet reviewed
    steps, _ = ExecutionPlanner().extend(wf(), [call("transfer_money", amount=10, payee_name="Rahul")],
                                         offered={"transfer_money": transfer}, all_tools={"transfer_money": transfer, "find_beneficiary": helper})
    assert [s.tool for s in steps] == ["transfer_money"]  # falls back to the single policy-gated step
