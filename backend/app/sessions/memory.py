"""Conversation memory shared by chat and voice: persisted messages, windowed history, rolling summary."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.database.models import Conversation, ConversationMessage
from app.database.session import Database
from app.domain import new_id
from app.security.redaction import strip_secrets


class ConversationMemory:
    def __init__(self, db: Database, window: int = 20) -> None:
        self.db = db
        self.window = window

    async def append(self, *, tenant_id: str, conversation_id: str, session_id: str, role: str, channel: str,
                     content: str, language: str | None = None, intent: str | None = None,
                     tool_calls: list[dict[str, Any]] | None = None, tool_call_id: str | None = None,
                     tool_name: str | None = None, sources: list[dict[str, Any]] | None = None,
                     interrupted: bool = False, extra: dict[str, Any] | None = None) -> str:
        msg_id = new_id()
        async with self.db.session() as s:
            s.add(ConversationMessage(
                id=msg_id, tenant_id=tenant_id, conversation_id=conversation_id, session_id=session_id, role=role,
                channel=channel, content=strip_secrets(content), language=language, intent=intent,
                tool_calls=tool_calls or [], tool_call_id=tool_call_id, tool_name=tool_name, sources=sources or [],
                interrupted=interrupted, extra=extra or {},
            ))
        return msg_id

    async def history(self, tenant_id: str, conversation_id: str, limit: int | None = None) -> list[ConversationMessage]:
        async with self.db.session() as s:
            rows = (await s.execute(
                select(ConversationMessage)
                .where(ConversationMessage.tenant_id == tenant_id, ConversationMessage.conversation_id == conversation_id,
                       ConversationMessage.role.in_(("user", "assistant", "human_agent")))
                .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
                .limit(limit or self.window)
            )).scalars().all()
        return list(reversed(rows))

    async def all_messages(self, tenant_id: str, conversation_id: str) -> list[ConversationMessage]:
        async with self.db.session() as s:
            return list((await s.execute(
                select(ConversationMessage)
                .where(ConversationMessage.tenant_id == tenant_id, ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.created_at, ConversationMessage.id)
            )).scalars())

    async def count(self, tenant_id: str, conversation_id: str) -> int:
        async with self.db.session() as s:
            return int((await s.execute(select(func.count()).select_from(ConversationMessage).where(
                ConversationMessage.tenant_id == tenant_id, ConversationMessage.conversation_id == conversation_id))).scalar_one())

    async def summary(self, tenant_id: str, conversation_id: str) -> str | None:
        async with self.db.session() as s:
            conv = await s.get(Conversation, conversation_id)
        return conv.summary if conv and conv.tenant_id == tenant_id else None

    async def set_summary(self, tenant_id: str, conversation_id: str, summary: str, intent: str | None = None) -> None:
        async with self.db.session() as s:
            conv = await s.get(Conversation, conversation_id)
            if conv and conv.tenant_id == tenant_id:
                conv.summary = summary
                if intent:
                    conv.last_intent = intent
