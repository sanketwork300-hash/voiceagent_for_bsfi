"""End-to-end prototype demo against a running backend (docker compose up).

    python -m scripts.demo [--base http://localhost:8000]

Shows: chat RAG, chat bank data (OpenAPI), voice Hinglish card outstanding (MCP) on the same conversation,
card blocking (policy + confirmation), ₹1,00,000 transfer (transaction OTP + confirmation), fraud handoff,
streaming WebSocket events, and the audit trail.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time

import httpx
import jwt
import websockets

TENANT = "demo-bank"
ASSERTION_SECRET = "demo-bank-idp-shared-secret-32bytes!"  # matches CUSTOMER_ASSERTION_SECRET in .env.example


def banner(t: str) -> None:
    print(f"\n\033[1;36m=== {t} ===\033[0m")


def show(user: str, r: dict, channel: str = "chat") -> None:
    tools = ", ".join(f"{t['name']}[{t['decision']}/{t['status']}]" for t in r.get("tool_calls", [])) or "-"
    pa = r.get("pending_action")
    print(f"\033[33m[{channel}] customer:\033[0m {user}")
    print(f"   intent={r.get('intent')}  auth={r.get('authentication_state')}  tools={tools}"
          + (f"  pending={pa['decision']}" if pa else "") + ("  HANDOFF" if r.get("handoff") else ""))
    print(f"\033[32m   agent:\033[0m {r['text']}")
    for s in r.get("sources", []):
        print(f"   source [{s['index']}] {s['title']} v{s['version']} p.{s['page']} — {s['section']}")


async def main(base: str) -> None:
    async with httpx.AsyncClient(base_url=base, timeout=30) as h:
        assertion = jwt.encode({"sub": "CUST1001", "aud": TENANT, "exp": int(time.time()) + 900, "amr": ["pwd", "otp"]},
                               ASSERTION_SECRET, algorithm="HS256")
        s = (await h.post("/sessions", json={"tenant": TENANT, "customer_assertion": assertion})).json()
        sid, tok = s["session"]["session_id"], s["session_token"]
        hdr = {"Authorization": f"Bearer {tok}"}

        async def chat(text: str) -> dict:
            r = (await h.post("/chat/message", json={"session_id": sid, "message": text}, headers=hdr)).json()
            show(text, r)
            return r

        async def voice(text: str) -> dict:
            r = (await h.post("/voice/simulate", json={"transcript": text}, headers=hdr)).json()
            show(text, r["response"], "voice")
            print(f"   \033[35mTTS:\033[0m {r['speech_text']}")
            return r

        banner("1. Chat — knowledge (RAG over Elasticsearch, with citations)")
        await chat("What are home loan foreclosure charges?")
        banner("2. Chat — customer data (mock bank OpenAPI tool)")
        await chat("What is my loan balance?")
        banner("3. Switch to voice — same conversation (LiveKit room + token issued)")
        v = (await h.post("/voice/session", json={}, headers=hdr)).json()
        print(f"   room={v['room']}  livekit_url={v['livekit_url']}  participant_token={v['participant_token'][:24]}…")
        await voice("Mera credit card ka outstanding kitna hai?")
        banner("4. Action — block card (policy -> confirmation -> MCP tool)")
        await chat("Block my card.")
        await chat("credit card")
        await chat("yes")
        banner("5. High-risk — transfer ₹1,00,000 (transaction OTP -> confirmation -> MCP)")
        await chat("Transfer ₹100,000 to Rahul.")
        await chat("123456")
        await chat("yes")

        banner("6. WebSocket streaming events")
        async with websockets.connect(f"{base.replace('http', 'ws', 1)}/ws/chat/{sid}?token={tok}") as ws:
            await ws.recv()  # session.ready
            await ws.send(json.dumps({"type": "message", "content": "Show my last transactions"}))
            while True:
                ev = json.loads(await ws.recv())
                if ev["type"] == "message.delta":
                    continue
                print(f"   event: {ev['type']}" + (f" ({ev.get('tool')})" if ev.get("tool") else ""))
                if ev["type"] == "typing" and ev.get("state") == "stopped":
                    break

        banner("7. Fraud — urgent handoff with context")
        await chat("Someone stole money from my account")
        login = (await h.post("/auth/login", json={"tenant": TENANT, "email": "agent@demo-bank.example",
                                                    "password": "DemoBank!2026secure"})).json()
        staff = {"Authorization": f"Bearer {login['access_token']}"}
        q = (await h.get("/handoff/queue", headers=staff)).json()
        mine = next(x for x in q if x["session_id"] == sid)
        print(f"   desk queue: priority={mine['priority']} reason={mine['reason']} summary={mine['context']['summary'][:120]}")

        banner("8. Audit trail (hash-chained)")
        aud = (await h.post("/auth/login", json={"tenant": TENANT, "email": "auditor@demo-bank.example",
                                                  "password": "DemoBank!2026secure"})).json()
        ah = {"Authorization": f"Bearer {aud['access_token']}"}
        for e in (await h.get("/audit/events", params={"session_id": sid, "limit": 12}, headers=ah)).json()[::-1]:
            print(f"   #{e['seq']} {e['event_type']:<30} {e['resource'] or '':<22} {e['outcome']}")
        print("   chain intact:", (await h.get("/audit/verify", headers=ah)).json()["intact"])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://localhost:8000")
    asyncio.run(main(p.parse_args().base))
