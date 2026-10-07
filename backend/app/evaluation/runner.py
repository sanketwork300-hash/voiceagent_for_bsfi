"""Runs the scenario suite against the chat channel and the voice channel adapter.

Voice runs feed the scenario text in as the STT transcript and check the spoken rendering, so the
whole voice path except audio I/O (LiveKit/STT/TTS vendors) is exercised.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import jwt
from sqlalchemy import select

from app.channels.chat.gateway import ChatGateway
from app.channels.voice.session import VoiceChannel
from app.database.models import ConversationMessage, EvaluationRun, Tenant
from app.domain import AgentResponse, AuthMethod, AuthState, Channel, new_id, utcnow
from app.evaluation.metrics import TurnResult, summarize
from app.evaluation.scenarios import SCENARIOS, Expect, Scenario

OTHER_TENANT_SLUG = "eval-isolation-tenant"


class EvaluationRunner:
    def __init__(self, container, reset_hook: Callable[[], Awaitable[None]] | None = None) -> None:
        self.c = container
        self.reset_hook = reset_hook

    async def _other_tenant(self) -> str:
        async with self.c.db.session() as s:
            t = (await s.execute(select(Tenant).where(Tenant.slug == OTHER_TENANT_SLUG))).scalar_one_or_none()
            if t is None:
                t = Tenant(id=new_id(), slug=OTHER_TENANT_SLUG, name="Isolation Test NBFC", institution_type="nbfc")
                s.add(t)
        return t.id

    async def _session(self, tenant_id: str, sc: Scenario, channel: Channel):
        state = await self.c.sessions.create(tenant_id=tenant_id, channel=channel)
        if sc.auth == "none":
            return state
        async with self.c.db.session() as s:
            slug = (await s.get(Tenant, tenant_id)).slug
        async with self.c.sessions.locked(state.session_id, tenant_id) as st:
            if sc.auth == "assertion":
                token = jwt.encode({"sub": sc.customer_id, "aud": slug, "exp": int(time.time()) + 600, "amr": ["pwd"]},
                                   self.c.settings.customer_assertion_secret, algorithm="HS256")
                await self.c.customer_auth.apply_assertion(st, token, slug)
            elif sc.auth == "voice_biometric":
                st.customer_id = sc.customer_id
                st.authentication_state = AuthState.IDENTIFIED
                st.auth_methods = [AuthMethod.CALLER_ID.value]
                await self.c.customer_auth.voice_biometric(st, 0.97)
        return state

    async def run(self, tenant_id: str, *, channels: list[Channel] | None = None, categories: list[str] | None = None,
                  scenarios: list[Scenario] | None = None, persist: bool = True) -> dict[str, Any]:
        channels = channels or [Channel.CHAT, Channel.VOICE]
        suite = [s for s in (scenarios or SCENARIOS) if not categories or s.category in categories]
        results: list[TurnResult] = []
        started = utcnow()
        for channel in channels:
            for sc in suite:
                if self.reset_hook:
                    await self.reset_hook()
                tid = await self._other_tenant() if sc.tenant == "other" else tenant_id
                state = await self._session(tid, sc, channel)
                voice = VoiceChannel(self.c.runtime, self.c.sessions, state.session_id, tid) if channel == Channel.VOICE else None
                chat = ChatGateway(self.c.runtime, self.c.sessions)
                for i, turn in enumerate(sc.turns):
                    t0 = time.perf_counter()
                    if voice:
                        resp = await voice.handle_utterance(turn.user)
                        spoken = voice.spoken[-1] if voice.spoken else ""
                    else:
                        resp = await chat.handle_message(session_id=state.session_id, tenant_id=tid, text=turn.user)
                        spoken = None
                    latency = (time.perf_counter() - t0) * 1000
                    results.append(self._check(sc, channel, i, turn.expect, resp, spoken, latency))
                if sc.check_storage_redacted:
                    results[-1].checks["pii_storage"] = await self._storage_clean(tid, state.conversation_id, sc.check_storage_redacted)
                    if not results[-1].checks["pii_storage"]:
                        results[-1].passed = False
                        results[-1].failures.append("secret persisted in conversation store")
        report = {"started_at": started.isoformat(), "finished_at": utcnow().isoformat(), "metrics": summarize(results),
                  "results": [r.__dict__ for r in results]}
        total = sum(1 for r in results if r.passed) / max(1, len(results))
        report["pass_rate"] = round(total, 3)
        if persist:
            async with self.c.db.session() as s:
                run = EvaluationRun(id=new_id(), tenant_id=tenant_id, channels=[c.value for c in channels], status="completed",
                                    started_at=started, finished_at=utcnow(), pass_rate=report["pass_rate"],
                                    metrics=report["metrics"], results=report["results"])
                s.add(run)
            report["id"] = run.id
        return report

    async def _storage_clean(self, tenant_id: str, conversation_id: str, forbidden: list[str]) -> bool:
        async with self.c.db.session() as s:
            rows = (await s.execute(select(ConversationMessage.content).where(
                ConversationMessage.tenant_id == tenant_id, ConversationMessage.conversation_id == conversation_id))).scalars().all()
        blob = "\n".join(rows).lower()
        return not any(f.lower() in blob for f in forbidden)

    @staticmethod
    def _check(sc: Scenario, channel: Channel, i: int, e: Expect, r: AgentResponse | None, spoken: str | None, latency: float) -> TurnResult:
        res = TurnResult(scenario=sc.key, channel=channel.value, turn=i, passed=True, latency_ms=round(latency, 1),
                         response=(r.text if r else "")[:300])
        if r is None:
            res.passed, res.failures = False, ["no response"]
            return res
        called = {t.name for t in r.tool_calls}
        decisions = {t.name: (t.decision.value if t.decision else None) for t in r.tool_calls}
        completed = {t.name for t in r.tool_calls if t.status == "completed"}
        text = r.text.lower()

        def check(name: str, ok: bool, why: str) -> None:
            res.checks[name] = res.checks.get(name, True) and ok
            if not ok:
                res.passed = False
                res.failures.append(why)

        if e.intent:
            check("intent", r.intent is not None and r.intent.value == e.intent, f"intent {r.intent} != {e.intent}")
        if e.tools:
            check("tool_selection", set(e.tools) <= called, f"tools {sorted(called)} missing {e.tools}")
        if e.decision:
            for tool, d in e.decision.items():
                check("authorization", decisions.get(tool) == d, f"{tool} decision {decisions.get(tool)} != {d}")
        for tool in e.not_executed:
            check("authorization", tool not in completed, f"{tool} executed but must not")
        if e.auth_state:
            check("authorization", r.authentication_state.value == e.auth_state, f"auth {r.authentication_state} != {e.auth_state}")
        if e.pending:
            got = r.pending_action.decision.value if r.pending_action else "none"
            check("flow", got == e.pending, f"pending {got} != {e.pending}")
        if e.handoff is not None:
            check("handoff", r.handoff == e.handoff, f"handoff {r.handoff} != {e.handoff}")
        if e.has_sources is not None:
            check("grounding", bool(r.sources) == e.has_sources, f"sources present={bool(r.sources)}")
        if e.contains_any:
            check("response", any(x.lower() in text for x in e.contains_any), f"response lacks any of {e.contains_any}")
        if e.not_contains:
            check("safety", not any(x.lower() in text for x in e.not_contains), f"response leaked {e.not_contains}")
        if spoken is not None and r.text:
            check("speech_rendering", "₹" not in spoken and "[1]" not in spoken, "speech text contains symbols/citations")
        return res
