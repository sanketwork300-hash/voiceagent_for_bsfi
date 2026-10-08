from __future__ import annotations

import pytest

from scripts.seed import DEMO_PASSWORD
from tests.conftest import customer_assertion


async def staff_token(api, email="admin@demo-bank.example") -> dict:
    r = await api.post("/auth/login", json={"tenant": "demo-bank", "email": email, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def customer_session(api, assertion: bool = True) -> tuple[str, dict]:
    body = {"tenant": "demo-bank", "channel": "chat"}
    if assertion:
        body["customer_assertion"] = customer_assertion()
    r = await api.post("/sessions", json=body)
    assert r.status_code == 201, r.text
    j = r.json()
    return j["session"]["session_id"], {"Authorization": f"Bearer {j['session_token']}"}


async def say(api, sid, hdr, text):
    r = await api.post("/chat/message", json={"session_id": sid, "message": text}, headers=hdr)
    assert r.status_code == 200, r.text
    return r.json()


async def test_health_ready_metrics(api):
    assert (await api.get("/health")).json() == {"status": "ok"}
    r = await api.get("/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"]
    assert "bfsi_turn_latency_seconds" in (await api.get("/metrics")).text


async def test_chat_rest_rag_and_bank_data(api):
    sid, hdr = await customer_session(api)
    kb = await say(api, sid, hdr, "What are home loan foreclosure charges?")
    assert kb["intent"] == "KNOWLEDGE_QUERY" and kb["sources"] and "[1]" in kb["text"]
    loan = await say(api, sid, hdr, "What is my loan balance?")
    assert "₹28,45,000" in loan["text"] and loan["tool_calls"][0]["name"] == "get_loan_details"


async def test_session_token_scoped_to_its_session(api):
    sid1, hdr1 = await customer_session(api)
    sid2, _ = await customer_session(api)
    r = await api.post("/chat/message", json={"session_id": sid2, "message": "hi"}, headers=hdr1)
    assert r.status_code == 404
    assert (await api.get(f"/sessions/{sid2}", headers=hdr1)).status_code == 404
    assert (await api.post("/chat/message", json={"session_id": sid1, "message": "hi"})).status_code == 401


async def test_anonymous_session_step_up_with_phone_and_otp(api):
    sid, hdr = await customer_session(api, assertion=False)
    r1 = await say(api, sid, hdr, "What is my account balance?")
    assert r1["pending_action"]["decision"] == "REQUIRE_AUTH" and "mobile number" in r1["text"]
    r2 = await say(api, sid, hdr, "my number is 9876543210")
    assert "one-time password" in r2["text"] and "XXXXXX3210" in r2["text"]
    r3 = await say(api, sid, hdr, "123456")
    assert r3["authentication_state"] == "FULLY_AUTHENTICATED" and "₹12,85,230.75" in r3["text"]


async def test_wrong_otp_lockout_triggers_handoff(api):
    sid, hdr = await customer_session(api)
    await say(api, sid, hdr, "Transfer ₹5,000 to Rohan")
    for _ in range(2):
        r = await say(api, sid, hdr, "000000")
        assert "didn't match" in r["text"]
    r = await say(api, sid, hdr, "111111")
    assert r["handoff"] and r["pending_action"] is None


async def test_chat_to_voice_switch_keeps_conversation(api, container):
    sid, hdr = await customer_session(api)
    await say(api, sid, hdr, "What is my loan balance?")
    r = await api.post("/voice/session", json={}, headers=hdr)
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["session"]["session_id"] == sid and v["session"]["channel"] == "voice" and v["participant_token"]
    from app.channels.voice.session import VoiceChannel

    st = await container.sessions.get(sid, v["session"]["session_id"] and (await container.sessions.get(sid, _tenant(container, sid))).tenant_id)
    voice = VoiceChannel(container.runtime, container.sessions, sid, st.tenant_id)
    resp = await voice.handle_utterance("Mera credit card ka outstanding kitna hai?")
    assert "23,450" in resp.text and "rupaye" in voice.spoken[-1]
    msgs = (await api.get(f"/sessions/{sid}/messages", headers=hdr)).json()
    assert {m["channel"] for m in msgs} == {"chat", "voice"}
    assert len({m["role"] for m in msgs}) == 2
    again = await say(api, sid, hdr, "thanks")  # back to chat, same conversation
    assert again["conversation_id"] == resp.conversation_id
    tok = await api.post("/voice/token", headers=hdr)
    assert tok.status_code == 200 and tok.json()["room"] == v["room"]


def _tenant(container, sid):
    return container.store._data[f"session:{sid}"][0].split('"tenant_id":"')[1].split('"')[0]


async def test_maker_checker_approval_end_to_end(api):
    sid, hdr = await customer_session(api)
    await say(api, sid, hdr, "Transfer ₹6,00,000 to Rahul")
    r = await say(api, sid, hdr, "123456")
    assert r["pending_action"]["decision"] == "REQUIRE_HUMAN_APPROVAL"
    sup = await staff_token(api, "supervisor@demo-bank.example")
    admin = await staff_token(api)
    pending = (await api.get("/approvals", headers=sup)).json()
    assert len(pending) == 1 and pending[0]["arguments"]["amount"] == 600000
    assert (await api.post(f"/approvals/{pending[0]['id']}/decision", json={"approve": True}, headers=admin)).status_code == 403
    assert (await api.post(f"/approvals/{pending[0]['id']}/decision", json={"approve": True}, headers=sup)).json()["status"] == "approved"
    r = await say(api, sid, hdr, "is it approved?")
    assert r["pending_action"]["decision"] == "REQUIRE_CONFIRMATION"
    r = await say(api, sid, hdr, "yes")
    assert "successful" in r["text"].lower() and "₹6,00,000" in r["text"]


async def test_handoff_desk_flow(api):
    sid, hdr = await customer_session(api)
    r = await say(api, sid, hdr, "I want to talk to a human agent")
    assert r["handoff"]
    agent = await staff_token(api, "agent@demo-bank.example")
    queue = (await api.get("/handoff/queue", headers=agent)).json()
    h = next(q for q in queue if q["session_id"] == sid)
    ctx = h["context"]
    assert {"conversation_id", "customer_id", "intent", "authentication_status", "summary", "tools_called", "actions_taken", "reason"} <= set(ctx)
    assert ctx["customer_id"] == "CUST1001" and ctx["reason"] == "CUSTOMER_REQUEST"
    await api.post(f"/handoff/{h['id']}/accept", headers=agent)
    relayed = await say(api, sid, hdr, "my card was charged twice")
    assert "passed your message" in relayed["text"]
    await api.post(f"/handoff/{h['id']}/reply", json={"text": "I'm reversing the duplicate charge now."}, headers=agent)
    await api.post(f"/handoff/{h['id']}/resolve", json={"resolution": "reversed", "return_to_agent": True}, headers=agent)
    back = await say(api, sid, hdr, "What is my loan balance?")
    assert "₹28,45,000" in back["text"] and not back["handoff"]
    msgs = (await api.get(f"/sessions/{sid}/messages", headers=hdr)).json()
    assert any(m["role"] == "human_agent" for m in msgs)


async def test_documents_versioning_and_search(api):
    hdr = await staff_token(api)
    docs = (await api.get("/documents", headers=hdr)).json()
    fees = next(d for d in docs if "Fees" in d["title"] or d["doc_type"] == "fees")
    v2 = b"Demo Bank Schedule of Charges\n\n1. ATM Transactions\nBeyond the free limit the ATM charge is Rs. 21 per transaction from 1 Nov 2026."
    r = await api.post("/documents", headers=hdr, files={"file": ("fees_v2.txt", v2, "text/plain")},
                       data={"document_id": fees["id"], "version": "2026.11"})
    assert r.status_code == 201 and r.json()["status"] == "indexed"
    res = (await api.post("/knowledge/search", json={"query": "ATM charge beyond free limit"}, headers=hdr)).json()["results"]
    fee_hits = [x for x in res if x["document_id"] == fees["id"]]
    assert fee_hits and all(x["version"] == "2026.11" for x in fee_hits)
    assert (await api.post(f"/documents/{fees['id']}/reindex", headers=hdr)).json()["status"] == "indexed"


async def test_tools_catalogue_and_test_endpoint(api):
    hdr = await staff_token(api)
    tools = {t["name"]: t for t in (await api.get("/tools", headers=hdr)).json()}
    assert tools["transfer_money"]["source"] == "mcp" and tools["get_loan_details"]["source"] == "openapi"
    assert tools["verify_otp"]["internal"]
    r = (await api.post("/tools/get_loan_details/test", json={"customer_id": "CUST1001"}, headers=hdr)).json()
    assert r["decision"]["decision"] == "ALLOW" and r["result"]["ok"]
    r = (await api.post("/tools/transfer_money/test", json={"customer_id": "CUST1001", "arguments": {"amount": 10, "payee_name": "Rahul"}},
                        headers=hdr)).json()
    assert r["decision"]["decision"] == "REQUIRE_AUTH" and not r["result"]["ok"]


async def test_integrations_mcp_and_audit(api):
    hdr = await staff_token(api)
    integ = (await api.get("/integrations", headers=hdr)).json()
    assert {i["kind"] for i in integ} == {"openapi", "mcp"}
    for i in integ:
        assert (await api.post(f"/integrations/{i['id']}/test", headers=hdr)).json()["ok"]
    auditor = await staff_token(api, "auditor@demo-bank.example")
    sid, chdr = await customer_session(api)
    await say(api, sid, chdr, "What is my loan balance?")
    events = (await api.get("/audit/events", headers=auditor)).json()
    assert any(e["event_type"] == "tool.executed" and e["resource"] == "get_loan_details" for e in events)
    assert (await api.get("/audit/verify", headers=auditor)).json()["intact"]
    assert (await api.get("/audit/events", headers=hdr)).status_code == 200  # tenant_admin has audit:read
    agent = await staff_token(api, "agent@demo-bank.example")
    assert (await api.get("/audit/events", headers=agent)).status_code == 403


async def test_policy_override_via_api(api):
    hdr = await staff_token(api)
    r = await api.post("/policies", headers=hdr, json={"name": "No transfers on voice above 2L", "effect": "DENY", "priority": 5,
                                                         "conditions": {"tool": "transfer_money", "channel": "voice", "args": {"amount": {"gt": 200000}}},
                                                         "params": {"reason": "Large transfers are not available over the phone."}})
    assert r.status_code == 201
    assert any(p["name"].startswith("No transfers on voice") for p in (await api.get("/policies", headers=hdr)).json()["effective"])
    bad = await api.post("/policies", headers=hdr, json={"name": "x", "effect": "ALLOW_EVERYTHING"})
    assert bad.status_code == 400


@pytest.mark.parametrize("path", ["/agents", "/documents", "/integrations", "/tools", "/policies", "/handoff/queue"])
async def test_admin_endpoints_require_auth(api, path):
    assert (await api.get(path)).status_code == 401


async def test_monitoring_and_mcp_listing(api):
    sid, hdr = await customer_session(api)
    await say(api, sid, hdr, "What is my loan balance?")
    await say(api, sid, hdr, "I want to talk to a human agent")
    admin = await staff_token(api)
    summary = (await api.get("/monitoring/summary", headers=admin)).json()
    assert summary["tools"]["completed"] >= 1 and summary["handoffs"]["total"] >= 1 and summary["series"]
    assert any(r["tool"] == "get_loan_details" for r in summary["tools"]["by_tool"])
    act = (await api.get("/monitoring/activity", headers=admin)).json()
    assert {a["kind"] for a in act} >= {"tool", "handoff"}
    servers = (await api.get("/mcp/servers", headers=admin)).json()
    assert servers[0]["tool_count"] == 3 and servers[0]["name"] == "mock-bank-mcp"


async def test_failure_outcome_reported(api, container):
    import httpx

    sid, hdr = await customer_session(api)

    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("down")

    container.rest_executor._client = httpx.AsyncClient(transport=Down())
    r = await say(api, sid, hdr, "What is my loan balance?")
    assert r["tool_calls"][0]["status"] == "failed"


async def test_failure_kind_distinguishes_rejected_from_unknown(container, authed_session):
    import httpx

    from app.tools.schemas import ToolCallRequest

    st = await authed_session()
    ctx = container.orchestrator._ctx(st, type("R", (), {"channel": st.channel, "request_id": "r1"})())
    tools = await container.registry.tools_for(st.tenant_id)
    call = ToolCallRequest(id="c", name="get_payment_status", arguments={"payment_ref": "NOPE123"})
    rejected = await container.gateway.execute(call, ctx, offered=tools)
    assert not rejected.result.ok and rejected.result.failure_kind == "rejected"  # bank said 404: nothing processed

    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ReadTimeout("slow")

    container.rest_executor._client = httpx.AsyncClient(transport=Down())
    unknown = await container.gateway.execute(call, ctx, offered=tools)
    assert unknown.result.failure_kind == "unknown"  # sent but unconfirmed: UI must not claim "not debited"


async def test_agent_update(api):
    hdr = await staff_token(api)
    agent = (await api.get("/agents", headers=hdr)).json()[0]
    r = await api.patch(f"/agents/{agent['id']}", headers=hdr, json={"allowed_tools": ["get_loan_details"], "voice_config": {"tts_provider": "sarvam"}})
    assert r.status_code == 200 and r.json()["allowed_tools"] == ["get_loan_details"]
    sid, chdr = await customer_session(api)
    bal = await say(api, sid, chdr, "What is my account balance?")  # balance tool no longer allowed for this agent
    assert not any(t["status"] == "completed" and t["name"] == "get_account_balance" for t in bal["tool_calls"])
    r = await api.patch(f"/agents/{agent['id']}", headers=hdr, json={"clear_tool_restriction": True})
    assert r.json()["allowed_tools"] is None
    agent_user = await staff_token(api, "agent@demo-bank.example")
    assert (await api.patch(f"/agents/{agent['id']}", headers=agent_user, json={"name": "x"})).status_code == 403
