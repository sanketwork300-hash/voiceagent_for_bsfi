"""LiveKit phone-number / SIP calls: identity hygiene, per-call session isolation, authentication is never weakened
by caller-id, call lifecycle (hangup never cancels a submitted transfer), barge-in safety and capacity limits."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.agents.execution.concurrency import SlotPool
from app.agents.execution.models import WorkflowStatus
from app.channels.voice.session import CallRejected
from app.channels.voice.telephony import (
    ATTR_CALLER,
    ATTR_CALL_ID,
    ATTR_DIALED,
    ATTR_TENANT,
    InboundCall,
    caller_ref,
    mask_digits,
    normalize_number,
    resolve_tenant,
)
from app.database.models import AuditEvent, Session as SessionRow, Tenant, ToolExecution, VoiceCall
from app.domain import AgentRequest, AuthState, Channel, SessionStatus
from app.llm.base import LLMError, LLMProvider, LLMResponse
from app.llm.provider import LLMCapacity, ResilientLLMProvider
from mock_bank import data as bank

RAW = "9876543210"  # CUST1001's registered mobile in the mock bank


def inbound(caller="+919876543210", room_suffix="a1", tenant="demo-bank", dialed="+14155550123"):
    return InboundCall.from_participant(
        f"call-_{caller}_{room_suffix}", f"sip_{caller}",
        {ATTR_CALLER: caller, ATTR_DIALED: dialed, ATTR_CALL_ID: f"SCL_{room_suffix}", ATTR_TENANT: tenant})


async def say(c, call, text):
    return await c.runtime.process(AgentRequest(tenant_id=call.tenant_id, session_id=call.session_id,
                                                channel=Channel.VOICE, message=text))


async def start(c, **kw):
    return await c.voice.start_inbound_call(inbound(**kw), await c.voice.admit())


# ------------------------------------------------------------------------------------------- identity hygiene
def test_number_normalisation_masking_and_caller_ref():
    assert normalize_number("098765 43210") == normalize_number("+91 98765-43210") == "+919876543210"
    assert mask_digits("call-_+919876543210_Xy3") == "call-_XXXXXX3210_Xy3" and mask_digits("sip_+919876543210") == "sip_XXXXXX3210"
    assert caller_ref("+919876543210", "k") == caller_ref("09876543210", "k") != caller_ref("+919876543210", "other-key")
    assert RAW not in repr(inbound())  # never printed


async def test_tenant_resolution_by_rule_attribute_or_dialled_number(container, tenant):
    assert (await resolve_tenant(container.db, inbound())).id == tenant.id  # bfsi.tenant set by our dispatch rule
    async with container.db.session() as s:
        t = await s.get(Tenant, tenant.id)
        t.settings = {**(t.settings or {}), "sip_numbers": ["+1 415 555 0123"]}
    by_number = InboundCall.from_participant("r", "sip_x", {ATTR_DIALED: "+14155550123"})
    assert (await resolve_tenant(container.db, by_number)).id == tenant.id
    assert await resolve_tenant(container.db, InboundCall.from_participant("r", "x", {ATTR_DIALED: "+15550000000"})) is None


async def test_unknown_number_is_rejected_and_capacity_released(container):
    admission = await container.voice.admit()
    with pytest.raises(CallRejected, match="unknown_number"):
        await container.voice.start_inbound_call(inbound(tenant="nope", dialed="+15550000000"), admission)
    await container.voice.release(admission)
    assert await container.slots.in_use("calls") == 0


# ------------------------------------------------------------------------------------------- per-call sessions
async def test_inbound_call_gets_its_own_session_and_stores_no_raw_number(container, tenant):
    call = await start(container)
    st = await container.sessions.get(call.session_id, tenant.id)
    assert st.channel == Channel.VOICE and st.call_id == call.call_id and st.customer_id == "CUST1001"
    assert st.authentication_state == AuthState.IDENTIFIED  # caller-id identifies; it never authenticates
    async with container.db.session() as s:
        row = await s.get(VoiceCall, call.call_id)
        sess_row = await s.get(SessionRow, call.session_id)
        audits = (await s.execute(select(AuditEvent).where(AuditEvent.session_id == call.session_id))).scalars().all()
        execs = (await s.execute(select(ToolExecution).where(ToolExecution.session_id == call.session_id))).scalars().all()
    assert row.transport == "sip" and row.status == "active" and row.caller_number_masked == "XXXXXX3210"
    assert row.caller_ref.startswith("ph_") and row.room_name == "call-_XXXXXX3210_a1" and row.sip_call_id == "SCL_a1"
    blobs = [json.dumps(row.__dict__, default=str), json.dumps(sess_row.state, default=str),
             await container.store.get(f"session:{call.session_id}"),
             *(json.dumps(a.payload, default=str) for a in audits), *(json.dumps(e.arguments, default=str) for e in execs)]
    assert all(RAW not in b for b in blobs), "raw caller number persisted"


async def test_known_caller_still_needs_otp_then_transaction_otp_and_confirmation(container, tenant):
    call = await start(container)
    r = await say(container, call, "What is my account balance?")
    assert r.pending_action and r.pending_action.decision == "REQUIRE_AUTH" and "₹12,85,230.75" not in r.text
    r = await say(container, call, "1 2 3 4 5 6")  # spoken OTP digits
    assert "₹12,85,230.75" in r.text and r.authentication_state == AuthState.FULLY_AUTHENTICATED
    r = await say(container, call, "Transfer ₹50,000 to Rahul")
    assert r.pending_action.decision == "REQUIRE_AUTH"  # transaction OTP bound to this exact transfer
    r = await say(container, call, "123456")
    assert r.pending_action.decision == "REQUIRE_CONFIRMATION"
    assert bank.DB["transfer_executions"] == 0


async def test_three_concurrent_callers_on_one_number_are_isolated(container, tenant):
    calls = await asyncio.gather(start(container, room_suffix="A"), start(container, caller="+919123456780", room_suffix="B"),
                                 start(container, room_suffix="C"))  # caller C is the same person as A, another call
    assert len({c.session_id for c in calls}) == 3 and len({c.call_id for c in calls}) == 3
    states = [await container.sessions.get(c.session_id, tenant.id) for c in calls]
    assert [s.customer_id for s in states] == ["CUST1001", "CUST1002", "CUST1001"]
    assert len({s.conversation_id for s in states}) == 3
    for c in calls:
        await say(container, c, "What is my account balance?")
    await asyncio.gather(*(say(container, c, "123456") for c in calls))
    rs = await asyncio.gather(*(say(container, c, "What is my account balance?") for c in calls))
    assert "₹12,85,230.75" in rs[0].text and "₹15,200" in rs[1].text and "₹12,85,230.75" in rs[2].text
    assert "₹15,200" not in rs[0].text and "₹12,85,230.75" not in rs[1].text
    # A's pending transfer is invisible to C (same customer, different call)
    await say(container, calls[0], "Transfer ₹1,000 to Rahul")
    await say(container, calls[0], "123456")
    await say(container, calls[2], "yes")
    assert bank.DB["transfer_executions"] == 0


# ------------------------------------------------------------------------------------------- lifecycle
async def test_hangup_cancels_only_unsubmitted_actions_and_closes_the_call(container, tenant):
    call = await start(container)
    await say(container, call, "Transfer ₹1,000 to Rahul")  # held for transaction OTP: not submitted
    st = await container.sessions.get(call.session_id, tenant.id)
    wf_id = st.active_workflow_id
    out = await container.voice.end_call(call, reason="caller_hangup")
    # caller-id only identified the caller, so the workflow was held at its first gated step (beneficiary lookup)
    assert out["pending_cancelled"] in ("find_beneficiary", "transfer_money") and out["closed"] and not out["unconfirmed"]
    assert (await container.engine.store.get(tenant.id, wf_id)).status == WorkflowStatus.CANCELLED
    async with container.db.session() as s:
        row = await s.get(VoiceCall, call.call_id)
        state = (await s.get(SessionRow, call.session_id)).state
    assert row.status == "completed" and row.end_reason == "caller_hangup" and row.ended_at and row.duration_seconds is not None
    assert state["status"] == SessionStatus.CLOSED and state["pending_action"] is None and state["auth_challenge"] is None
    assert await container.voice.end_call(call) == {}  # idempotent
    assert await container.slots.in_use("calls") == 0 and await container.slots.in_use("stt") == 0


async def _call_at_confirmation(container):
    call = await start(container)
    await say(container, call, "What is my account balance?")
    await say(container, call, "123456")
    await say(container, call, "Transfer ₹1,000 to Rahul")
    r = await say(container, call, "123456")
    assert r.pending_action.decision == "REQUIRE_CONFIRMATION"
    return call


async def _barge_in_during_transfer(container, call):
    bank.FAULTS["transfer_money"] = {"mode": "delay", "seconds": 0.4, "count": 1}  # slow bank, then it commits
    task = asyncio.create_task(say(container, call, "yes"))
    for _ in range(100):  # wait until the transfer is on the wire
        await asyncio.sleep(0.02)
        if container.engine.inflight.pending(call.session_id):
            break
    task.cancel()  # caller interrupts / hangs up while the bank is processing
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_hangup_mid_transfer_never_cancels_the_submitted_transfer(container, tenant):
    call = await _call_at_confirmation(container)
    await _barge_in_during_transfer(container, call)
    out = await container.voice.end_call(call, reason="caller_hangup")
    assert bank.DB["transfer_executions"] == 1 and bank.DB["duplicates_prevented"] == 0
    assert not out["unconfirmed"] and out["closed"]
    async with container.db.session() as s:
        ex = (await s.execute(select(ToolExecution).where(ToolExecution.tool_name == "transfer_money",
                                                          ToolExecution.status == "completed"))).scalars().all()
    assert len(ex) == 1  # recorded although nobody was listening any more


async def test_wait_after_submission_reports_status_and_never_reverses(container, tenant):
    call = await _call_at_confirmation(container)
    await _barge_in_during_transfer(container, call)
    r = await say(container, call, "Wait!")
    assert "already been sent to the bank" in r.text and "confirmed it was completed" in r.text and "can't reverse" in r.text
    assert bank.DB["transfer_executions"] == 1
    async with container.db.session() as s:
        ev = (await s.execute(select(AuditEvent).where(AuditEvent.event_type == "action.stop_requested_after_submission"))).scalars().all()
    assert ev


async def test_interrupted_outcome_is_told_on_the_next_turn(container, tenant):
    call = await _call_at_confirmation(container)
    await _barge_in_during_transfer(container, call)
    r = await say(container, call, "What is my account balance?")
    assert "bank has confirmed it was completed" in r.text  # the result the caller never heard
    assert "₹12,84,230.75" in r.text  # ... and the new question is answered too


async def test_hangup_with_unconfirmed_transfer_creates_a_human_follow_up(container, tenant):
    async def unknown(*_a, **_k):
        from app.tools.schemas import ToolResult
        return ToolResult(ok=False, error="status service down", failure_kind="unknown", failure_category="DEPENDENCY_UNAVAILABLE")

    call = await _call_at_confirmation(container)
    await _barge_in_during_transfer(container, call)
    s = await container.sessions.get(call.session_id, tenant.id)
    wf = await container.engine.store.get(tenant.id, s.last_action_workflow_id)
    m = next(x for x in wf.steps if x.tool == "transfer_money")
    m.status = m.status.__class__.UNKNOWN  # as if the response had been lost
    for x in wf.steps:
        if x.verifies == m.id:
            x.status = x.status.__class__.PENDING
    wf.reported = False
    await container.engine.store.save(wf)
    async with container.sessions.locked(call.session_id, tenant.id) as st:
        st.active_workflow_id = wf.workflow_id
    container.gateway.verify = unknown  # and the status lookup is unavailable
    out = await container.voice.end_call(call, reason="caller_hangup")
    assert out["unconfirmed"] and out["handoff_id"] and not out["closed"]
    assert bank.DB["transfer_executions"] == 1  # still exactly once: nothing was resent


# ------------------------------------------------------------------------------------------- capacity
async def test_max_active_calls_rejects_gracefully(container):
    container.voice.settings = container.settings.model_copy(update={"max_active_calls": 2})
    a, b = await container.voice.admit(), await container.voice.admit()
    with pytest.raises(CallRejected, match="capacity:calls"):
        await container.voice.admit()
    await container.voice.release(a)
    c = await container.voice.admit()  # a freed line is usable again
    for x in (b, c):
        await container.voice.release(x)


async def test_stt_tts_streams_are_reserved_per_call(container):
    container.voice.settings = container.settings.model_copy(update={"max_concurrent_tts_requests": 1})
    a = await container.voice.admit()
    with pytest.raises(CallRejected, match="capacity:tts"):
        await container.voice.admit()
    assert await container.slots.in_use("calls") == 1  # the rejected call did not keep its partial reservation
    await container.voice.release(a)


class _Slow(LLMProvider):
    name = "slow"

    async def complete(self, messages, **kw):
        await asyncio.sleep(0.3)
        return LLMResponse(content="primary")


class _Fallback(LLMProvider):
    name = "fallback"

    async def complete(self, messages, **kw):
        return LLMResponse(content="fallback")


async def test_llm_concurrency_limit_fails_over_instead_of_queueing():
    pool = SlotPool()
    llm = ResilientLLMProvider(_Slow(), _Fallback(), LLMCapacity(pool=pool, limit=1, queue_timeout=0.05, lease_ttl=5))
    a, b = await asyncio.gather(llm.complete([]), llm.complete([]))
    assert sorted([a.content, b.content]) == ["fallback", "primary"]
    alone = ResilientLLMProvider(_Slow(), None, LLMCapacity(pool=pool, limit=1, queue_timeout=0.05, lease_ttl=5))
    results = await asyncio.gather(alone.complete([]), alone.complete([]), return_exceptions=True)
    assert sum(isinstance(r, LLMError) for r in results) == 1  # no fallback: a clean error, not a hang


# ------------------------------------------------------------------------------------------- LiveKit worker glue
async def test_worker_bootstrap_binds_a_sip_participant_to_a_new_session(container, tenant, monkeypatch):
    lk = pytest.importorskip("app.channels.voice.livekit_agent")
    participant = SimpleNamespace(identity="sip_+919876543210", attributes=dict(inbound().attributes))

    async def wait_for_participant():
        return participant

    ctx = SimpleNamespace(job=SimpleNamespace(metadata=""), room=SimpleNamespace(name="call-_+919876543210_zz"),
                          wait_for_participant=wait_for_participant)
    call, p = await lk._bootstrap_call(ctx, container)
    assert p is participant and call.transport == "sip"
    st = await container.sessions.get(call.session_id, tenant.id)
    assert st.call_id == call.call_id and st.voice_room is None  # the raw room name (with the number) is not persisted
    load = lk._worker_load(SimpleNamespace(active_jobs=[1, 2, 3]))
    assert load == pytest.approx(3 / container.settings.max_agent_sessions_per_worker)
