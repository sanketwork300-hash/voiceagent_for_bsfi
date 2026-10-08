"""Operational summaries for dashboards, computed from persisted tool executions, handoffs, audit and evaluations.

Voice media metrics (STT/TTS latency, interruptions) live in Prometheus (`/metrics`, worker), not in the database,
so they are reported as null here rather than estimated.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import mean

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import container
from app.auth.authorization import StaffPrincipal, require
from app.auth.permissions import Permission
from app.database.models import (
    AuditEvent,
    Conversation,
    ConversationMessage,
    EvaluationRun,
    Handoff,
    Session,
    ToolExecution,
)
from app.domain import utcnow

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))], 1)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=utcnow().tzinfo)


@router.get("/summary")
async def summary(hours: int = Query(24, ge=1, le=24 * 30), p: StaffPrincipal = Depends(require(Permission.TENANT_READ)),
                  c=Depends(container)) -> dict:
    now = utcnow()
    since = now - timedelta(hours=hours)
    t = p.tenant_id
    async with c.db.session() as s:
        execs = (await s.execute(select(ToolExecution).where(ToolExecution.tenant_id == t, ToolExecution.created_at >= since)
                                 .order_by(ToolExecution.created_at.desc()).limit(20000))).scalars().all()
        convs = (await s.execute(select(Conversation).where(Conversation.tenant_id == t, Conversation.created_at >= since))).scalars().all()
        handoffs = (await s.execute(select(Handoff).where(Handoff.tenant_id == t, Handoff.created_at >= since))).scalars().all()
        sessions = (await s.execute(select(Session).where(Session.tenant_id == t, Session.status == "ACTIVE",
                                                          Session.last_activity_at >= now - timedelta(minutes=30)))).scalars().all()
        auth = (await s.execute(select(AuditEvent.event_type).where(
            AuditEvent.tenant_id == t, AuditEvent.occurred_at >= since,
            AuditEvent.event_type.in_(("auth.otp_verified", "auth.otp_failed", "auth.assertion_accepted"))))).scalars().all()
        intents = (await s.execute(select(ConversationMessage.intent).where(
            ConversationMessage.tenant_id == t, ConversationMessage.created_at >= since, ConversationMessage.role == "assistant",
            ConversationMessage.intent.is_not(None)))).scalars().all()
        last_eval = (await s.execute(select(EvaluationRun).where(EvaluationRun.tenant_id == t)
                                     .order_by(EvaluationRun.created_at.desc()).limit(1))).scalar_one_or_none()

    completed = [e for e in execs if e.status == "completed"]
    failed = [e for e in execs if e.status == "failed"]
    by_tool: dict[str, list[ToolExecution]] = defaultdict(list)
    for e in execs:
        by_tool[e.tool_name].append(e)
    buckets: dict[str, dict] = {}
    step = max(1, hours // 24)
    for i in range(hours // step, -1, -1):
        b = (now - timedelta(hours=i * step)).replace(minute=0, second=0, microsecond=0)
        buckets[b.isoformat()] = {"t": b.isoformat(), "calls": 0, "failures": 0, "latency": []}
    for e in execs:
        ts = _aware(e.created_at).replace(minute=0, second=0, microsecond=0)
        ts = ts - timedelta(hours=ts.hour % step)
        b = buckets.get(ts.isoformat())
        if b:
            b["calls"] += 1
            b["failures"] += e.status == "failed"
            if e.latency_ms is not None:
                b["latency"].append(e.latency_ms)
    series = [{"t": b["t"], "calls": b["calls"], "failures": b["failures"],
               "latency_ms": round(mean(b["latency"]), 1) if b["latency"] else None} for b in buckets.values()]
    auth_c = Counter(auth)
    otp_total = auth_c["auth.otp_verified"] + auth_c["auth.otp_failed"]
    handoff_convs = {h.conversation_id for h in handoffs}
    eval_metrics = (last_eval.metrics or {}) if last_eval else {}
    grounding = [m.get("accuracy", {}).get("grounding") for m in eval_metrics.values() if isinstance(m, dict)]
    grounding = [g for g in grounding if g is not None]
    return {
        "window_hours": hours,
        "active_sessions": {"total": len(sessions), "chat": sum(x.channel == "chat" for x in sessions),
                            "voice": sum(x.channel == "voice" for x in sessions)},
        "conversations": {"total": len(convs), "chat": sum(x.channel == "chat" for x in convs),
                          "voice": sum(x.channel == "voice" for x in convs),
                          "escalated": len(handoff_convs), "resolved_without_escalation": len([x for x in convs if x.id not in handoff_convs])},
        "handoffs": {"total": len(handoffs), "by_reason": dict(Counter(h.reason for h in handoffs)),
                     "rate": round(len(handoff_convs) / len(convs), 3) if convs else None},
        "tools": {
            "total": len(execs), "completed": len(completed), "failed": len(failed),
            "denied": sum(e.status == "denied" for e in execs), "pending": sum(e.status == "pending" for e in execs),
            "success_rate": round(len(completed) / (len(completed) + len(failed)), 3) if completed or failed else None,
            "latency_ms": {"avg": round(mean([e.latency_ms for e in completed if e.latency_ms is not None]), 1)
                           if any(e.latency_ms is not None for e in completed) else None,
                           "p95": _p95([e.latency_ms for e in completed if e.latency_ms is not None])},
            "by_tool": sorted([{"tool": k, "source": v[0].source_type, "calls": len(v),
                                "failures": sum(e.status == "failed" for e in v), "denied": sum(e.status == "denied" for e in v),
                                "p95_ms": _p95([e.latency_ms for e in v if e.latency_ms is not None])}
                               for k, v in by_tool.items()], key=lambda r: -r["calls"]),
        },
        "policy_decisions": dict(Counter(e.policy_decision for e in execs if e.policy_decision)),
        "authentication": {"otp_verified": auth_c["auth.otp_verified"], "otp_failed": auth_c["auth.otp_failed"],
                           "assertions": auth_c["auth.assertion_accepted"],
                           "success_rate": round(auth_c["auth.otp_verified"] / otp_total, 3) if otp_total else None},
        "intents": dict(Counter(intents).most_common(8)),
        "knowledge_grounding": round(mean(grounding), 3) if grounding else None,
        "last_evaluation": {"id": last_eval.id, "pass_rate": last_eval.pass_rate, "at": last_eval.created_at} if last_eval else None,
        "series": series,
        "voice_media": None,  # STT/TTS/interruption metrics are exported by the voice worker via Prometheus
    }


@router.get("/activity")
async def activity(limit: int = Query(30, ge=1, le=200), p: StaffPrincipal = Depends(require(Permission.TENANT_READ)),
                   c=Depends(container)) -> list[dict]:
    t = p.tenant_id
    async with c.db.session() as s:
        execs = (await s.execute(select(ToolExecution).where(ToolExecution.tenant_id == t)
                                 .order_by(ToolExecution.created_at.desc()).limit(limit))).scalars().all()
        hand = (await s.execute(select(Handoff).where(Handoff.tenant_id == t).order_by(Handoff.created_at.desc()).limit(10))).scalars().all()
        auth = (await s.execute(select(AuditEvent).where(AuditEvent.tenant_id == t, AuditEvent.event_type.like("auth.%"))
                                .order_by(AuditEvent.seq.desc()).limit(10))).scalars().all()
    items = [{"id": e.id, "kind": "tool", "at": _aware(e.created_at), "title": e.tool_name, "status": e.status, "channel": e.channel,
              "latency_ms": e.latency_ms, "session_id": e.session_id, "detail": e.policy_decision} for e in execs]
    items += [{"id": h.id, "kind": "handoff", "at": _aware(h.created_at), "title": "Human handoff", "status": h.status,
               "channel": h.channel, "session_id": h.session_id, "detail": f"{h.reason} · {h.priority}"} for h in hand]
    items += [{"id": a.id, "kind": "auth", "at": _aware(a.occurred_at), "title": a.event_type, "status": a.outcome,
               "channel": a.channel, "session_id": a.session_id, "detail": None} for a in auth]
    return sorted(items, key=lambda x: x["at"], reverse=True)[:limit]
