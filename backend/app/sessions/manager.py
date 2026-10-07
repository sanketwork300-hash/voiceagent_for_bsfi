"""Session lifecycle + hot state store (Redis in production, in-process for tests/dev).

Hot state lives in the store with an idle TTL; every mutation is mirrored to PostgreSQL so a session
survives Redis eviction and is auditable. A per-session lock serialises turns, so a voice turn and a
chat message arriving at the same time cannot interleave.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select

from app.agents.state import SessionState
from app.database.models import Conversation
from app.database.models import Session as SessionRow
from app.database.session import Database
from app.domain import AuthState, Channel, SessionStatus, new_id, utcnow

log = logging.getLogger(__name__)


class SessionNotFound(LookupError):
    pass


class StateStore(ABC):
    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str, ttl: int) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    def lock(self, key: str, timeout: float = 30) -> Any: ...

    @abstractmethod
    async def publish(self, channel: str, message: dict[str, Any]) -> None: ...

    @abstractmethod
    def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]: ...

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:  # noqa: B027
        pass


class InMemoryStateStore(StateStore):
    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    async def get(self, key):
        item = self._data.get(key)
        if not item:
            return None
        if item[1] < time.monotonic():
            self._data.pop(key, None)
            return None
        return item[0]

    async def set(self, key, value, ttl):
        self._data[key] = (value, time.monotonic() + ttl)

    async def delete(self, key):
        self._data.pop(key, None)

    def lock(self, key, timeout=30):
        return self._locks[key]

    async def publish(self, channel, message):
        for q in list(self._subs[channel]):
            q.put_nowait(message)

    async def subscribe(self, channel):
        q: asyncio.Queue = asyncio.Queue()
        self._subs[channel].append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs[channel].remove(q)


class RedisStateStore(StateStore):
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self.redis = redis.from_url(url, decode_responses=True)

    async def get(self, key):
        return await self.redis.get(key)

    async def set(self, key, value, ttl):
        await self.redis.set(key, value, ex=ttl)

    async def delete(self, key):
        await self.redis.delete(key)

    def lock(self, key, timeout=30):
        return self.redis.lock(f"lock:{key}", timeout=timeout, blocking_timeout=timeout)

    async def publish(self, channel, message):
        await self.redis.publish(channel, json.dumps(message, default=str))

    async def subscribe(self, channel):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    yield json.loads(msg["data"])
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    async def ping(self):
        return bool(await self.redis.ping())

    async def aclose(self):
        await self.redis.aclose()


class SessionManager:
    def __init__(self, db: Database, store: StateStore, idle_ttl_seconds: int = 1800) -> None:
        self.db = db
        self.store = store
        self.ttl = idle_ttl_seconds
        self._listeners: list[Callable[[SessionState], Any]] = []

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}"

    async def create(self, *, tenant_id: str, channel: Channel, agent_id: str | None = None, customer_id: str | None = None,
                     auth_state: AuthState = AuthState.UNAUTHENTICATED, auth_methods: list[str] | None = None,
                     language: str = "en", conversation_id: str | None = None) -> SessionState:
        async with self.db.session() as s:
            if conversation_id:
                conv = (await s.execute(select(Conversation).where(
                    Conversation.tenant_id == tenant_id, Conversation.id == conversation_id))).scalar_one_or_none()
                if conv is None:
                    raise SessionNotFound("conversation not found")
            else:
                conv = Conversation(id=new_id(), tenant_id=tenant_id, agent_id=agent_id, customer_ref=customer_id,
                                    channel=channel.value, channels_used=[channel.value], language=language)
                s.add(conv)
                await s.flush()
            state = SessionState(session_id=new_id(), tenant_id=tenant_id, conversation_id=conv.id, agent_id=agent_id,
                                 customer_id=customer_id, channel=channel, active_channels=[channel], language=language,
                                 authentication_state=auth_state, auth_methods=auth_methods or [])
            s.add(SessionRow(id=state.session_id, tenant_id=tenant_id, conversation_id=conv.id, agent_id=agent_id,
                             customer_ref=customer_id, channel=channel.value, language=language,
                             authentication_state=auth_state.value, state=_snapshot(state), last_activity_at=utcnow()))
        await self.store.set(self._key(state.session_id), state.model_dump_json(), self.ttl)
        return state

    async def get(self, session_id: str, tenant_id: str) -> SessionState:
        raw = await self.store.get(self._key(session_id))
        if raw:
            state = SessionState.model_validate_json(raw)
        else:  # cold path: rehydrate from the durable mirror
            async with self.db.session() as s:
                row = await s.get(SessionRow, session_id)
            if row is None or not row.state:
                raise SessionNotFound(session_id)
            state = SessionState.model_validate(row.state)
        if state.tenant_id != tenant_id:  # never reveal that the session exists in another tenant
            raise SessionNotFound(session_id)
        if state.status == SessionStatus.CLOSED:
            raise SessionNotFound(session_id)
        return state

    async def save(self, state: SessionState) -> None:
        state.last_activity_at = utcnow()
        await self.store.set(self._key(state.session_id), state.model_dump_json(), self.ttl)
        async with self.db.session() as s:
            row = await s.get(SessionRow, state.session_id)
            if row is not None:
                row.channel = state.channel.value
                row.language = state.language
                row.authentication_state = state.authentication_state.value
                row.current_intent = state.current_intent.value if state.current_intent else None
                row.status = state.status.value
                row.customer_ref = state.customer_id
                row.state = _snapshot(state)
                row.last_activity_at = state.last_activity_at
        for cb in self._listeners:
            try:
                cb(state)
            except Exception:
                log.exception("session listener failed")

    @asynccontextmanager
    async def locked(self, session_id: str, tenant_id: str) -> AsyncIterator[SessionState]:
        async with self.store.lock(self._key(session_id)):
            state = await self.get(session_id, tenant_id)
            yield state
            await self.save(state)

    async def attach_channel(self, session_id: str, tenant_id: str, channel: Channel) -> SessionState:
        """Channel switch: same session + conversation, new active channel."""
        async with self.locked(session_id, tenant_id) as state:
            state.channel = channel
            if channel not in state.active_channels:
                state.active_channels.append(channel)
        async with self.db.session() as s:
            conv = await s.get(Conversation, state.conversation_id)
            if conv and channel.value not in (conv.channels_used or []):
                conv.channels_used = [*(conv.channels_used or []), channel.value]
        return state

    async def close(self, session_id: str, tenant_id: str) -> None:
        async with self.locked(session_id, tenant_id) as state:
            state.status = SessionStatus.CLOSED
            state.pending_action = None
            state.auth_challenge = None
        await self.store.delete(self._key(session_id))


def _snapshot(state: SessionState) -> dict[str, Any]:
    return json.loads(state.model_dump_json())
