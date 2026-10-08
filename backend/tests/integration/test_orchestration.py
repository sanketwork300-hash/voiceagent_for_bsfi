"""Integration tests for the orchestration upgrade: multi-tool turns, DAG workflows, financial safety, verification,
idempotency, restart recovery and session concurrency — through the real runtime, gateway, policy engine and mock bank."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from sqlalchemy import select

from app.agents.execution.models import StepStatus, WorkflowStatus
from app.container import Container
from app.database.models import AgentWorkflow, ConversationMessage, ToolExecution
from app.domain import AgentRequest, Channel, SessionStatus
from app.llm.base import LLMProvider, LLMResponse, LLMToolCall
from app.sessions.manager import InMemoryStateStore
from mock_bank import data as bank
from mock_bank.app import app as bank_app
from tests.conftest import customer_assertion, make_settings


async def turn(c, st, text, channel=Channel.CHAT):
    events, response = [], None
    async for ev in c.runtime.stream(AgentRequest(tenant_id=st.tenant_id, session_id=st.session_id, channel=channel, message=text)):
        events.append(ev)
        if ev.type == "message.completed":
            response = ev.response
    return response, events


def fault(tool, mode, seconds=1.0, count=1):
    bank.FAULTS[tool] = {"mode": mode, "seconds": seconds, "count": count}


def set_timeout(c, tenant_id, **timeouts):
    def hook(tools):
        for name, t in timeouts.items():
            if name in tools:
                tools[name].timeout_seconds = t
    c.registry.add_hook(hook)
    c.registry.invalidate(tenant_id)


async def to_confirmation(c, st, text="Transfer ₹1,000 to Rahul"):
    r, _ = await turn(c, st, text)
    assert r.pending_action and r.pending_action.decision == "REQUIRE_AUTH"
    r, _ = await turn(c, st, "123456")
    assert r.pending_action and r.pending_action.decision == "REQUIRE_CONFIRMATION"
    return r


class ScriptedLLM(LLMProvider):
    name = "scripted"

    def __init__(self, script, fallback):
        self.script, self.fallback, self.seen = list(script), fallback, []

    async def complete(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None, json_schema=None):
        if json_schema:
            return await self.fallback.complete(messages, json_schema=json_schema)
        self.seen.append(messages)
        if messages[-1].role == "tool" or not self.script:
            return await self.fallback.complete(messages, tools=tools)
        return self.script.pop(0)


@pytest.fixture
def scripted(container):
    real = container.orchestrator.llm

    def install(*responses):
        llm = ScriptedLLM(responses, real)
        container.orchestrator.llm = llm
        return llm

    yield install
    container.orchestrator.llm = real


def calls(*items):
    return LLMResponse(tool_calls=[LLMToolCall(id=f"c{i}_{n}", name=n, arguments=a) for i, (n, a) in enumerate(items)])


# ------------------------------------------------------------------------------------------- A: parallel reads
async def test_independent_reads_execute_in_parallel(container, authed_session):
    st = await authed_session()
    fault("get_account_balance", "delay", 0.4)
    fault("get_recent_transactions", "delay", 0.4)
    t0 = time.perf_counter()
    r, events = await turn(container, st, "What is my balance and my recent transactions?")
    elapsed = time.perf_counter() - t0
    assert {t.name for t in r.tool_calls} == {"get_account_balance", "get_recent_transactions"}
    assert all(t.status == "completed" for t in r.tool_calls)
    assert "₹12,85,230.75" in r.text and "Swiggy" in r.text
    started = [e for e in events if e.type == "tool.started"]
    assert len({e.data["parallel_group"] for e in started}) == 1  # one wave
    assert elapsed < 0.75, f"reads ran sequentially ({elapsed:.2f}s)"


async def test_every_llm_call_gets_its_own_structured_tool_message(container, authed_session, scripted):
    st = await authed_session()
    llm = scripted(calls(("get_account_balance", {}), ("get_loan_details", {}), ("transfer_money", {"amount": "lots"})))
    await turn(container, st, "Show my balance and loan")
    msgs = llm.seen[-1]
    tool_msgs = {m.tool_call_id: json.loads(m.content) for m in msgs if m.role == "tool"}
    assert set(tool_msgs) == {"c0_get_account_balance", "c1_get_loan_details", "c2_transfer_money"}
    assert tool_msgs["c0_get_account_balance"]["status"] == "COMPLETED" and tool_msgs["c0_get_account_balance"]["ok"]
    assert not tool_msgs["c2_transfer_money"]["ok"]  # not offered for a data query / invalid: never executed


async def test_policy_gates_each_parallel_step_and_one_login_unlocks_them(api):
    from tests.integration.test_api_flows import customer_session, say

    sid, hdr = await customer_session(api, assertion=False)
    r1 = await say(api, sid, hdr, "What is my account balance and my recent transactions?")
    assert r1["pending_action"]["decision"] == "REQUIRE_AUTH" and all(t["status"] == "pending" for t in r1["tool_calls"])
    await say(api, sid, hdr, "my number is 9876543210")
    r3 = await say(api, sid, hdr, "123456")
    assert "₹12,85,230.75" in r3["text"] and "Swiggy" in r3["text"]
    assert {t["name"] for t in r3["tool_calls"] if t["status"] == "completed"} == {"get_account_balance", "get_recent_transactions"}


# ------------------------------------------------------------------------------------------- B: dependent workflow
async def test_transfer_runs_as_verified_dependent_workflow(container, authed_session):
    st = await authed_session()
    r, events = await turn(container, st, "Transfer ₹1,000 to Rahul")
    assert [t.name for t in r.tool_calls] == ["find_beneficiary", "transfer_money"]
    assert "Rahul Verma, account XXXX4521" in r.pending_action.summary  # confirms the resolved beneficiary
    r, _ = await turn(container, st, "123456")
    assert r.pending_action.decision == "REQUIRE_CONFIRMATION"
    r, events = await turn(container, st, "yes")
    assert "Transfer successful" in r.text and "Rahul Verma" in r.text
    ver = [e for e in events if e.type == "verification.completed"]
    assert ver and ver[0].data["status"] == "SUCCESS" and ver[0].data["method"] == "status_lookup"
    order = [e.type for e in events if e.type in ("tool.completed", "verification.completed")]
    assert order.index("verification.completed") < order.index("tool.completed")  # success only after verification
    assert bank.DB["transfer_executions"] == 1
    async with container.db.session() as s:
        ex = (await s.execute(select(ToolExecution).where(ToolExecution.tool_name == "transfer_money",
                                                          ToolExecution.status == "completed"))).scalar_one()
        wf = (await s.execute(select(AgentWorkflow).where(AgentWorkflow.id == ex.workflow_id))).scalar_one()
    assert ex.idempotency_key and f"transfer_money:{ex.idempotency_key}" in bank.IDEMPOTENCY  # propagated to the bank
    assert wf.status == WorkflowStatus.COMPLETED and ex.arguments.get("beneficiary_id") == "BEN01"


async def test_unknown_beneficiary_stops_before_any_money_moves(container, authed_session):
    st = await authed_session()
    r, _ = await turn(container, st, "Transfer ₹1,000 to Zubin")
    assert r.pending_action is None and bank.DB["transfer_executions"] == 0
    assert "No registered beneficiary" in r.text


async def test_llm_cannot_supply_the_workflow_bound_beneficiary(container, authed_session, scripted):
    st = await authed_session()
    scripted(calls(("transfer_money", {"amount": 1000, "payee_name": "Rahul", "beneficiary_id": "BEN02"})))
    r, _ = await turn(container, st, "Transfer ₹1,000 to Rahul")
    assert r.pending_action is not None
    async with container.db.session() as s:
        from app.database.models import AuditEvent
        ev = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "security.injected_param_override_attempt"))).scalars().all()
    assert any(e.payload["params"] == ["beneficiary_id"] for e in ev)
    pa = (await container.sessions.get(st.session_id, st.tenant_id)).pending_action
    assert pa.arguments["beneficiary_id"] == "BEN01"  # resolved server-side, not the model's BEN02


# ------------------------------------------------------------------------------------------- financial safety
async def test_two_transfers_in_one_response_only_one_is_planned(container, authed_session, scripted):
    st = await authed_session()
    scripted(calls(("transfer_money", {"amount": 1000, "payee_name": "Rahul"}),
                   ("transfer_money", {"amount": 2000, "payee_name": "Rohan"})))
    r, _ = await turn(container, st, "Transfer ₹1,000 to Rahul and ₹2,000 to Rohan")
    statuses = {(t.name, t.arguments.get("amount")): t.status for t in r.tool_calls if t.name == "transfer_money"}
    assert statuses[("transfer_money", 2000)] == "denied" and statuses[("transfer_money", 1000)] == "pending"


async def test_yes_but_different_amount_never_executes_and_needs_fresh_confirmation(container, authed_session):
    st = await authed_session()
    first = await to_confirmation(container, st)
    r, _ = await turn(container, st, "Yes, but make it ₹50,000")
    assert bank.DB["transfer_executions"] == 0  # neither ₹1,000 nor ₹50,000 moved
    assert r.pending_action and "50,000" in r.pending_action.summary and r.pending_action.id != first.pending_action.id
    assert r.pending_action.decision == "REQUIRE_AUTH"  # new action: new transaction OTP, then a new confirmation
    r, _ = await turn(container, st, "123456")
    assert r.pending_action.decision == "REQUIRE_CONFIRMATION" and "50,000" in r.pending_action.summary
    r, _ = await turn(container, st, "yes")
    assert bank.DB["transfer_executions"] == 1 and bank.DB["payments"]["CUST1001"][-1]["amount"] == 50000


async def test_unclear_confirmation_keeps_the_action_frozen(container, authed_session):
    st = await authed_session()
    await to_confirmation(container, st)
    r, _ = await turn(container, st, "yes yes go ahead and do it right now please")
    assert bank.DB["transfer_executions"] == 0 and r.pending_action.decision == "REQUIRE_CONFIRMATION"
    r, _ = await turn(container, st, "yes")
    assert bank.DB["transfer_executions"] == 1


# ------------------------------------------------------------------------------------------- retries / verification
async def test_read_timeout_is_retried_with_backoff(container, authed_session, tenant):
    set_timeout(container, tenant.id, get_account_balance=0.3)
    fault("get_account_balance", "delay", 1.0, count=1)
    st = await authed_session()
    r, _ = await turn(container, st, "What is my account balance?")
    assert "₹12,85,230.75" in r.text
    async with container.db.session() as s:
        ex = (await s.execute(select(ToolExecution).where(ToolExecution.tool_name == "get_account_balance"))).scalar_one()
    assert ex.attempts == 2 and ex.status == "completed"


async def test_transfer_timeout_is_verified_never_retried(container, authed_session, tenant):
    set_timeout(container, tenant.id, transfer_money=0.3)
    st = await authed_session()
    await to_confirmation(container, st)
    fault("transfer_money", "timeout_after_commit", 1.0)  # the bank commits, the response is lost
    r, events = await turn(container, st, "yes")
    assert bank.DB["transfer_executions"] == 1 and bank.DB["duplicates_prevented"] == 0  # no resend at all
    assert "Transfer successful" in r.text  # recovered from the system of record
    assert any(e.type == "verification.completed" and e.data["status"] == "SUCCESS" for e in events)
    async with container.db.session() as s:
        ex = (await s.execute(select(ToolExecution).where(ToolExecution.tool_name == "transfer_money",
                                                          ToolExecution.policy_decision == "ALLOW"))).scalar_one()
    assert ex.attempts == 1 and ex.failure_category == "TIMEOUT"


async def test_transfer_lost_before_commit_is_reported_not_processed(container, authed_session, tenant):
    set_timeout(container, tenant.id, transfer_money=0.3)
    container.engine.verifier.inflight_grace, container.engine.verifier.interval = 0.0, 0.05
    st = await authed_session()
    await to_confirmation(container, st)
    balance = bank.DB["accounts"]["CUST1001"][0]["available_balance"]
    fault("transfer_money", "timeout_before_commit", 1.0)
    r, _ = await turn(container, st, "yes")
    assert "was not processed" in r.text and "successful" not in r.text.lower()
    assert bank.DB["transfer_executions"] == 0 and bank.DB["accounts"]["CUST1001"][0]["available_balance"] == balance


async def test_unconfirmable_outcome_escalates_without_resending(container, authed_session, tenant):
    set_timeout(container, tenant.id, transfer_money=0.3)
    container.engine.verifier.interval = 0.01
    st = await authed_session()
    await to_confirmation(container, st)
    fault("transfer_money", "timeout_after_commit", 1.0)
    fault("get_transfer_status", "error_500", count=10)  # the status API is down too
    r, _ = await turn(container, st, "yes")
    assert "couldn't confirm" in r.text and "not sent it again" in r.text and r.handoff
    assert bank.DB["transfer_executions"] == 1 and bank.DB["duplicates_prevented"] == 0


async def test_bank_deduplicates_a_resent_idempotency_key(container, authed_session):
    st = await authed_session()
    await to_confirmation(container, st)
    await turn(container, st, "yes")
    key = next(k for k in bank.IDEMPOTENCY if k.startswith("transfer_money:")).split(":", 1)[1]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bank_app), base_url="http://mock-bank") as client:
        init = await client.post("/mcp", headers={"X-API-Key": "mock-bank-api-key"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        sid = init.headers["mcp-session-id"]
        again = await client.post("/mcp", headers={"X-API-Key": "mock-bank-api-key", "Mcp-Session-Id": sid}, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "transfer_money", "arguments": {"customer_id": "CUST1001", "amount": 1000, "payee_name": "Rahul"},
                "_meta": {"idempotency_key": key}}})
    assert again.json()["result"]["structuredContent"]["status"] == "SUCCESS"
    assert bank.DB["transfer_executions"] == 1 and bank.DB["duplicates_prevented"] == 1


# ------------------------------------------------------------------------------------------- restart recovery
class WorkerCrashed(BaseException):
    pass


async def test_worker_crash_after_send_is_reconciled_by_another_worker(settings, tenant, container, authed_session):
    st = await authed_session()
    await to_confirmation(container, st)
    durable = await container.store.get(f"session:{st.session_id}")  # what Redis holds before the crashing turn
    version = await container.store.get(f"session:{st.session_id}:v")

    original = container.engine.executor._apply_outcome

    def crash_after_send(wf, step, outcome):
        if step.tool == "transfer_money" and outcome.result.ok:
            raise WorkerCrashed()  # the process dies after the bank committed, before recording the result
        return original(wf, step, outcome)

    container.engine.executor._apply_outcome = crash_after_send
    with pytest.raises(WorkerCrashed):
        await turn(container, st, "yes")
    container.engine.executor._apply_outcome = original
    await container.store.set(f"session:{st.session_id}", durable, 600)  # the crashed worker never saved the session
    await container.store.set(f"session:{st.session_id}:v", version, 600)
    assert bank.DB["transfer_executions"] == 1

    worker_b = Container(settings, http_transport=httpx.ASGITransport(app=bank_app), store=container.store)
    await worker_b.startup()
    try:
        r, _ = await turn(worker_b, st, "yes")  # the customer repeats "yes" on another worker
        assert "bank has confirmed it was completed" in r.text
        assert bank.DB["transfer_executions"] == 1 and bank.DB["duplicates_prevented"] == 0
        s = await worker_b.sessions.get(st.session_id, st.tenant_id)
        assert s.pending_action is None and s.active_workflow_id is None
    finally:
        await worker_b.shutdown()


# ------------------------------------------------------------------------------------------- sessions & workers
async def test_hundred_concurrent_sessions_are_isolated(container, tenant):
    sessions = []
    for i in range(100):
        st = await container.sessions.create(tenant_id=tenant.id, channel=Channel.CHAT)
        cust = "CUST1001" if i % 2 == 0 else "CUST1002"
        async with container.sessions.locked(st.session_id, tenant.id) as s:
            await container.customer_auth.apply_assertion(s, customer_assertion(cust), "demo-bank")
        sessions.append((st, cust))
    results = await asyncio.gather(*(turn(container, st, "What is my account balance?") for st, _ in sessions))
    for (_, cust), (r, _) in zip(sessions, results, strict=True):
        mine, theirs = ("₹12,85,230.75", "₹15,200") if cust == "CUST1001" else ("₹15,200", "₹12,85,230.75")
        assert mine in r.text and theirs not in r.text


async def test_ten_concurrent_requests_on_one_session_are_serialized(container, authed_session):
    st = await authed_session()
    before = (await container.sessions.get(st.session_id, st.tenant_id)).version
    results = await asyncio.gather(*(turn(container, st, f"What is my account balance? #{i}") for i in range(10)))
    assert all("₹12,85,230.75" in r.text for r, _ in results)
    async with container.db.session() as s:
        rows = (await s.execute(select(ConversationMessage).where(ConversationMessage.conversation_id == st.conversation_id)
                                .order_by(ConversationMessage.created_at))).scalars().all()
    roles = [m.role for m in rows]
    assert roles == ["user", "assistant"] * 10  # never interleaved
    assert (await container.sessions.get(st.session_id, st.tenant_id)).version == before + 10  # no lost update


async def test_session_busy_beyond_wait_is_rejected_not_interleaved(container, authed_session):
    st = await authed_session()
    container.sessions.lock_wait = 0.05
    async with container.sessions.locked(st.session_id, st.tenant_id):
        r, _ = await turn(container, st, "What is my account balance?")
    assert r.error == "session_busy" and "previous message" in r.text


async def test_three_workers_share_sessions_without_leaking(settings, tenant):
    store = InMemoryStateStore()  # stands in for Redis shared by all workers (see the BFSI_INFRA_TESTS variant)
    workers = []
    for _ in range(3):
        c = Container(settings, http_transport=httpx.ASGITransport(app=bank_app), store=store)
        await c.startup()
        workers.append(c)
    try:
        callers = []
        for cust in ("CUST1001", "CUST1002", "CUST1001"):
            st = await workers[0].sessions.create(tenant_id=tenant.id, channel=Channel.VOICE)
            async with workers[0].sessions.locked(st.session_id, tenant.id) as s:
                await workers[0].customer_auth.apply_assertion(s, customer_assertion(cust), "demo-bank")
            callers.append((st, cust))
        # caller A -> worker 1, B -> worker 2, C -> worker 3, all at once
        out = await asyncio.gather(*(turn(w, st, "What is my account balance?", Channel.VOICE)
                                     for w, (st, _) in zip(workers, callers, strict=True)))
        assert "₹12,85,230.75" in out[0][0].text and "₹15,200" in out[1][0].text and "₹12,85,230.75" in out[2][0].text
        # caller A's transfer starts on worker 1, the OTP lands on worker 2, the confirmation on worker 3
        a = callers[0][0]
        r, _ = await turn(workers[0], a, "Transfer ₹1,000 to Rahul")
        assert r.pending_action.decision == "REQUIRE_AUTH"
        r, _ = await turn(workers[1], a, "123456")
        assert r.pending_action.decision == "REQUIRE_CONFIRMATION"
        r, _ = await turn(workers[2], a, "yes")
        assert "Transfer successful" in r.text and bank.DB["transfer_executions"] == 1
        # caller C (same customer, different session) has no pending action and cannot confirm A's
        r, _ = await turn(workers[2], callers[2][0], "yes")
        assert bank.DB["transfer_executions"] == 1
        st_c = await workers[2].sessions.get(callers[2][0].session_id, tenant.id)
        assert st_c.pending_action is None and st_c.status == SessionStatus.ACTIVE
    finally:
        for w in workers:
            await w.shutdown()


async def test_workflow_state_survives_and_held_steps_resume_on_another_worker(settings, tenant, container, authed_session):
    st = await authed_session()
    await turn(container, st, "Transfer ₹1,000 to Rahul")
    s = await container.sessions.get(st.session_id, st.tenant_id)
    wf = await container.engine.store.get(tenant.id, s.active_workflow_id)
    assert wf and wf.status == WorkflowStatus.WAITING_FOR_AUTH
    assert {x.tool: x.status for x in wf.steps if x.tool}["transfer_money"] == StepStatus.HELD
    other = Container(settings, http_transport=httpx.ASGITransport(app=bank_app), store=container.store)
    await other.startup()
    try:
        r, _ = await turn(other, st, "123456")
        assert r.pending_action.decision == "REQUIRE_CONFIRMATION"
        r, _ = await turn(other, st, "no")
        assert bank.DB["transfer_executions"] == 0
        assert (await other.engine.store.get(tenant.id, wf.workflow_id)).status == WorkflowStatus.CANCELLED
    finally:
        await other.shutdown()


def test_settings_validation_rejects_unsafe_production_config(tmp_path):
    from app.config import Settings

    base = make_settings(tmp_path).model_dump()
    with pytest.raises(ValueError, match="REDIS_URL is required"):
        Settings(**{**base, "environment": "production", "redis_url": None})
    with pytest.raises(ValueError, match="WORKFLOW_TIMEOUT"):
        Settings(**{**base, "workflow_timeout": 5, "default_tool_timeout": 10})
    legacy = {k: v for k, v in base.items() if k != "max_agent_iterations"}
    assert Settings(**{**legacy, "llm_max_tool_iterations": 6}).max_agent_iterations == 6  # legacy name still honoured


# ------------------------------------------------------------------------------------------- second-audit regressions
async def test_resume_does_not_offer_tools_the_model_was_not_offered(container, authed_session, scripted):
    st = await authed_session()
    scripted(calls(("transfer_money", {"amount": 1000, "payee_name": "Rahul"}), ("verify_otp", {"challenge_id": "x", "otp": "1"})))
    await turn(container, st, "Transfer ₹1,000 to Rahul")
    await turn(container, st, "123456")
    r, _ = await turn(container, st, "yes")
    assert "Transfer successful" in r.text
    async with container.db.session() as s:
        from app.database.models import AuditEvent
        blocked = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "security.tool_not_offered",
                                                            AuditEvent.resource == "verify_otp"))).scalars().all()
    assert blocked  # the queued call stayed "not offered" when the held workflow resumed


async def test_mutation_is_not_sent_if_write_ahead_cannot_be_persisted(container, authed_session):
    st = await authed_session()
    await to_confirmation(container, st)
    original = container.engine.store.save

    async def failing_save(wf):
        if any(x.status == StepStatus.SUBMITTING for x in wf.steps):
            raise RuntimeError("database unavailable")
        return await original(wf)

    container.engine.store.save = failing_save
    container.engine.executor.store = container.engine.store
    try:
        r, _ = await turn(container, st, "yes")
    finally:
        container.engine.store.save = original
    assert bank.DB["transfer_executions"] == 0 and "successful" not in r.text.lower()


async def test_beneficiary_id_is_never_model_input_even_without_the_template(container, authed_session, tenant, scripted):
    from app.database.models import MCPTool

    async with container.db.session() as s:
        row = (await s.execute(select(MCPTool).where(MCPTool.tenant_id == tenant.id, MCPTool.name == "find_beneficiary"))).scalar_one()
        row.is_enabled = False  # helper not available -> no template
    container.registry.invalidate(tenant.id)
    tools = await container.registry.tools_for(tenant.id)
    assert "beneficiary_id" not in tools["transfer_money"].llm_schema()["properties"]
    st = await authed_session()
    scripted(calls(("transfer_money", {"amount": 1000, "payee_name": "Rahul", "beneficiary_id": "BEN02"})))
    await turn(container, st, "Transfer ₹1,000 to Rahul")
    pa = (await container.sessions.get(st.session_id, st.tenant_id)).pending_action
    assert "beneficiary_id" not in pa.arguments  # stripped: the bank resolves "Rahul" itself
