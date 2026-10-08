"""Session lifecycle + hot state store (Redis in production, in-process for tests/dev).

Hot state lives in the store with an idle TTL; every mutation is mirrored to PostgreSQL so a session
survives Redis eviction and is auditable. A per-session lock serialises turns, so a voice turn and a
chat message arriving at the same time cannot interleave — on one worker or across many:

* `RedisStateStore.lock` is a Redis lock (SET NX PX + token) with a TTL that is *renewed* while the turn runs, so a
  long turn keeps it and a crashed worker's lock expires. A request that cannot get it within `wait` seconds gets
  `SessionBusy` instead of queueing forever.
* Saves made under the lock are compare-and-set on a version counter: if the lock was ever lost (e.g. a GC pause
  longer than the TTL) and another worker wrote meanwhile, the stale write is rejected instead of clobbering it.
* `InMemoryStateStore` gives the same semantics inside one process (dev/test only — it is not shared across workers).
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
from app.observability import metrics

log = logging.getLogger(__name__)


class SessionNotFound(LookupError):
    pass


class SessionBusy(RuntimeError):
    """Another request holds this session's lock for longer than the configured wait."""


class StateConflict(RuntimeError):
    """A versioned save found a newer version (the lock was lost and someone else wrote)."""


class SessionLock:
    """Async context manager returned by `StateStore.lock`. `lost` is set if renewal failed mid-turn."""

    lost: bool = False

    async def __aenter__(self) -> SessionLock:
        raise NotImplementedError

    async def __aexit__(self, *exc: Any) -> None:
        raise NotImplementedError


class StateStore(ABC):
    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, value: str, ttl: int) -> None: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    def lock(self, key: str, timeout: float = 30, wait: float | None = None) -> SessionLock:
        """`timeout`: lock TTL (renewed while held). `wait`: max seconds to wait for it (default: `timeout`)."""

    @abstractmethod
    async def set_versioned(self, key: str, value: str, ttl: int, expected_version: int) -> bool:
        """Write only if the stored version equals `expected_version` (absent counts as a match); bumps it."""

    @abstractmethod
    async def publish(self, channel: str, message: dict[str, Any]) -> None: ...

    @abstractmethod
    def subscribe(self, channel: str) -> AsyncIterator[dict[str, Any]]: ...

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:  # noqa: B027
        pass


class _LocalLock(SessionLock):
    def __init__(self, lock: asyncio.Lock, wait: float) -> None:
        self._lock, self._wait = lock, wait

    async def __aenter__(self) -> _LocalLock:
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=self._wait)
        except TimeoutError as e:
            raise SessionBusy("session is busy") from e
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._lock.release()


class InMemoryStateStore(StateStore):
    """Single-process store for dev/tests. Locks and data are NOT shared across worker processes."""

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

    async def set_versioned(self, key, value, ttl, expected_version):
        # no await between check and write: atomic within the event loop
        current = await self.get(f"{key}:v")
        if current is not None and int(current) != expected_version:
            return False
        expires = time.monotonic() + ttl
        self._data[key] = (value, expires)
        self._data[f"{key}:v"] = (str(expected_version + 1), expires)
        return True

    async def delete(self, key):
        self._data.pop(key, None)
        self._data.pop(f"{key}:v", None)

    def lock(self, key, timeout=30, wait=None):
        return _LocalLock(self._locks[key], timeout if wait is None else wait)

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


# KEYS[1] data, KEYS[2] version. ARGV: value, expected_version, ttl_seconds
_CAS_SET = """
local cur = redis.call('GET', KEYS[2])
if cur and tonumber(cur) ~= tonumber(ARGV[2]) then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
redis.call('SET', KEYS[2], tonumber(ARGV[2]) + 1, 'EX', ARGV[3])
return 1
"""


class _RedisLock(SessionLock):
    def __init__(self, redis_client: Any, name: str, ttl: float, wait: float) -> None:
        self._lock = redis_client.lock(name, timeout=ttl, blocking=True, blocking_timeout=wait, thread_local=False)
        self._ttl = ttl
        self._renewer: asyncio.Task | None = None
        self.lost = False

    async def __aenter__(self) -> _RedisLock:
        if not await self._lock.acquire():
            raise SessionBusy("session is busy")
        self._renewer = asyncio.create_task(self._renew())
        return self

    async def _renew(self) -> None:
        from redis.exceptions import LockError

        try:
            while True:
                await asyncio.sleep(self._ttl / 3)
                await self._lock.reacquire()  # reset the TTL; fails if the token no longer matches
        except asyncio.CancelledError:
            raise
        except (LockError, Exception):  # noqa: BLE001 - connection problems also mean we may lose the lock
            self.lost = True
            log.error("session lock renewal failed; the next save will be version-checked", extra={"lock": self._lock.name})

    async def __aexit__(self, *exc: Any) -> None:
        from redis.exceptions import LockError

        if self._renewer:
            self._renewer.cancel()
        try:
            await self._lock.release()
        except LockError:
            self.lost = True  # expired and possibly taken by another worker


class RedisStateStore(StateStore):
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self.redis = redis.from_url(url, decode_responses=True)
        self._cas = self.redis.register_script(_CAS_SET)

    async def get(self, key):
        return await self.redis.get(key)

    async def set(self, key, value, ttl):
        await self.redis.set(key, value, ex=ttl)

    async def set_versioned(self, key, value, ttl, expected_version):
        return bool(await self._cas(keys=[key, f"{key}:v"], args=[value, int(expected_version), int(ttl)]))

    async def delete(self, key):
        await self.redis.delete(key, f"{key}:v")

    def lock(self, key, timeout=30, wait=None):
        return _RedisLock(self.redis, f"lock:{key}", timeout, timeout if wait is None else wait)

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
    def __init__(self, db: Database, store: StateStore, idle_ttl_seconds: int = 1800, *, lock_ttl: float = 30.0,
                 lock_wait: float = 20.0) -> None:
        self.db = db
        self.store = store
        self.ttl = idle_ttl_seconds
        self.lock_ttl = lock_ttl
        self.lock_wait = lock_wait
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
        state.version = 1
        await self.store.set_versioned(self._key(state.session_id), state.model_dump_json(), self.ttl, 0)
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

    async def save(self, state: SessionState, *, expected_version: int | None = None) -> None:
        """With `expected_version` (used under the session lock) the write is compare-and-set; raises StateConflict."""
        state.last_activity_at = utcnow()
        key = self._key(state.session_id)
        if expected_version is None:  # unconditional (outside the lock): still advances the version for CAS readers
            state.version += 1
            await self.store.set(key, state.model_dump_json(), self.ttl)
            await self.store.set(f"{key}:v", str(state.version), self.ttl)
        else:
            state.version = expected_version + 1
            if not await self.store.set_versioned(key, state.model_dump_json(), self.ttl, expected_version):
                state.version = expected_version
                metrics.session_state_conflicts.inc()
                raise StateConflict(state.session_id)
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
    async def locked(self, session_id: str, tenant_id: str, *, wait: float | None = None) -> AsyncIterator[SessionState]:
        """Serialise all work on one session across every worker. Raises SessionBusy if the lock stays taken.

        State is saved even when the body is cancelled (barge-in) or fails, so effects that already happened
        (OTP sent, transfer submitted, counters) are never forgotten; the save itself is shielded from cancellation.
        """
        started = time.monotonic()
        try:
            lock_cm = self.store.lock(self._key(session_id), timeout=self.lock_ttl, wait=self.lock_wait if wait is None else wait)
            async with lock_cm as lock:
                metrics.session_lock_wait.observe(time.monotonic() - started)
                state = await self.get(session_id, tenant_id)
                loaded_version = state.version
                try:
                    yield state
                finally:
                    try:
                        await asyncio.shield(self.save(state, expected_version=loaded_version))
                    except StateConflict:
                        log.error("session state changed under a lost lock; this turn's state was not saved",
                                  extra={"session_id": session_id, "lock_lost": getattr(lock, "lost", None)})
        except SessionBusy:
            metrics.session_busy.inc()
            raise

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
