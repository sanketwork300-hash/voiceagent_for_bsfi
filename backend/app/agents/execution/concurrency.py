"""Concurrency limits for tool execution.

Two layers, deliberately separate:

* **In-process** (`ToolConcurrencyManager`): asyncio semaphores — global (per worker process), per concurrency group,
  per tool and per session. These bound what *one worker* sends at once. They are NOT cluster-wide: with N workers the
  effective global ceiling is N x `TOOL_GLOBAL_CONCURRENCY`.
* **Distributed** (`RedisLeaseLimiter`, opt-in via `DISTRIBUTED_TOOL_LIMITS=true`): a Redis sorted set of expiring
  leases per concurrency group, so e.g. "at most 16 concurrent calls to the core-banking API" holds across all workers.
  Leases expire on their own (TTL), so a crashed worker cannot leak capacity forever.

Slots are always acquired in the same order (global -> group -> distributed group -> tool -> session) and released in
reverse, so two steps can never deadlock waiting on each other's semaphores.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from app.observability import metrics


class ConcurrencyLimitTimeout(TimeoutError):
    def __init__(self, scope: str) -> None:
        super().__init__(f"timed out waiting for a {scope} concurrency slot")
        self.scope = scope


class _RefSemaphore:
    __slots__ = ("sem", "refs")

    def __init__(self, n: int) -> None:
        self.sem = asyncio.Semaphore(n)
        self.refs = 0


# KEYS[1] lease zset. ARGV: limit, ttl_ms, token. Uses the Redis server clock so worker clock skew does not matter.
_LEASE_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[1]) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
  redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
  return 1
end
return 0
"""
# KEYS[1] lease zset. ARGV: ttl_ms, token. Extends a held lease (long calls); 0 if it already expired.
_LEASE_RENEW = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
if redis.call('ZSCORE', KEYS[1], ARGV[2]) == false then return 0 end
redis.call('ZADD', KEYS[1], 'XX', now + tonumber(ARGV[1]), ARGV[2])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[1]) * 2)
return 1
"""


class SlotPool:
    """Named counting pools of expiring leases ("at most N active calls / LLM requests / STT streams").

    With a Redis client the pools are cluster-wide (sorted set per pool, Redis server clock); without one they are
    per process (dev/tests). Leases expire on their own, so a crashed worker cannot leak capacity; long holders renew.
    """

    def __init__(self, redis_client: Any | None = None, prefix: str = "slots") -> None:
        self.redis = redis_client
        self.prefix = prefix
        self._local: dict[str, dict[str, float]] = {}
        if redis_client is not None:
            self._acquire_script = redis_client.register_script(_LEASE_ACQUIRE)
            self._renew_script = redis_client.register_script(_LEASE_RENEW)

    @property
    def distributed(self) -> bool:
        return self.redis is not None

    async def try_acquire(self, name: str, limit: int, *, ttl: float) -> str | None:
        token = uuid.uuid4().hex
        if self.redis is not None:
            ok = await self._acquire_script(keys=[f"{self.prefix}:{name}"], args=[limit, int(ttl * 1000), token])
            return token if ok else None
        now = time.monotonic()
        pool = {t: exp for t, exp in self._local.get(name, {}).items() if exp > now}
        self._local[name] = pool
        if len(pool) >= limit:
            return None
        pool[token] = now + ttl
        return token

    async def acquire(self, name: str, limit: int, *, ttl: float, timeout: float, poll: float = 0.05,
                      scope: str | None = None) -> str:
        deadline = time.monotonic() + timeout
        waited = False
        while (token := await self.try_acquire(name, limit, ttl=ttl)) is None:
            if not waited:
                metrics.concurrency_limit_hits.labels(scope or name.split(":")[-1]).inc()
                waited = True
            if time.monotonic() >= deadline:
                raise ConcurrencyLimitTimeout(scope or name)
            await asyncio.sleep(poll)
        return token

    async def renew(self, name: str, token: str, *, ttl: float) -> bool:
        if self.redis is not None:
            return bool(await self._renew_script(keys=[f"{self.prefix}:{name}"], args=[int(ttl * 1000), token]))
        pool = self._local.get(name, {})
        if token not in pool:
            return False
        pool[token] = time.monotonic() + ttl
        return True

    async def release(self, name: str, token: str) -> None:
        if self.redis is not None:
            await self.redis.zrem(f"{self.prefix}:{name}", token)
        else:
            self._local.get(name, {}).pop(token, None)

    async def in_use(self, name: str) -> int:
        if self.redis is not None:
            key = f"{self.prefix}:{name}"
            now_ms = int(time.time() * 1000)
            await self.redis.zremrangebyscore(key, "-inf", now_ms)
            return int(await self.redis.zcard(key))
        now = time.monotonic()
        return sum(1 for exp in self._local.get(name, {}).values() if exp > now)

    @asynccontextmanager
    async def slot(self, name: str, limit: int, *, ttl: float, timeout: float, scope: str | None = None) -> AsyncIterator[None]:
        token = await self.acquire(name, limit, ttl=ttl, timeout=timeout, scope=scope)
        try:
            yield
        finally:
            await self.release(name, token)


class RedisLeaseLimiter:
    """Cluster-wide counting semaphore built on expiring leases in a Redis sorted set (tool concurrency groups)."""

    def __init__(self, redis_client: Any, prefix: str = "toollimit") -> None:
        self.pool = SlotPool(redis_client, prefix=prefix)

    @asynccontextmanager
    async def lease(self, name: str, limit: int, *, ttl: float, timeout: float, poll: float = 0.05) -> AsyncIterator[None]:
        async with self.pool.slot(name, limit, ttl=ttl, timeout=timeout, scope="distributed_group"):
            yield


class ToolConcurrencyManager:
    def __init__(self, *, global_limit: int, group_limits: dict[str, int] | None = None, session_limit: int = 4,
                 distributed: RedisLeaseLimiter | None = None) -> None:
        self.global_limit = global_limit
        self.group_limits = dict(group_limits or {})
        self.session_limit = session_limit
        self.distributed = distributed
        self._global = asyncio.Semaphore(global_limit)
        self._keyed: dict[str, _RefSemaphore] = {}

    @asynccontextmanager
    async def _keyed_slot(self, key: str, limit: int, scope: str, deadline: float) -> AsyncIterator[None]:
        entry = self._keyed.get(key)
        if entry is None:
            entry = self._keyed[key] = _RefSemaphore(limit)
        entry.refs += 1
        try:
            await _acquire(entry.sem, scope, deadline)
            try:
                yield
            finally:
                entry.sem.release()
        finally:
            entry.refs -= 1
            if entry.refs == 0:  # no holders or waiters: forget it (sessions come and go)
                self._keyed.pop(key, None)

    @asynccontextmanager
    async def slot(self, *, tenant_id: str, session_id: str, tool: str, group: str | None, tool_limit: int | None,
                   timeout: float, lease_ttl: float = 30.0) -> AsyncIterator[None]:
        deadline = time.monotonic() + timeout
        async with AsyncExitStack() as stack:
            await _acquire(self._global, "global", deadline)
            stack.callback(self._global.release)
            group_limit = self.group_limits.get(group) if group else None
            if group and group_limit:
                await stack.enter_async_context(self._keyed_slot(f"g:{tenant_id}:{group}", group_limit, "group", deadline))
                if self.distributed is not None:
                    await stack.enter_async_context(self.distributed.lease(
                        f"{tenant_id}:{group}", group_limit, ttl=lease_ttl, timeout=max(0.0, deadline - time.monotonic())))
            if tool_limit:
                await stack.enter_async_context(self._keyed_slot(f"t:{tenant_id}:{tool}", tool_limit, "tool", deadline))
            await stack.enter_async_context(self._keyed_slot(f"s:{session_id}", self.session_limit, "session", deadline))
            yield

    def snapshot(self) -> dict[str, Any]:
        return {"global_available": self._global._value, "keyed": len(self._keyed)}  # noqa: SLF001 - diagnostics


async def _acquire(sem: asyncio.Semaphore, scope: str, deadline: float) -> None:
    if sem.locked():
        metrics.concurrency_limit_hits.labels(scope).inc()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ConcurrencyLimitTimeout(scope)
    try:
        await asyncio.wait_for(sem.acquire(), timeout=remaining)
    except TimeoutError as e:
        raise ConcurrencyLimitTimeout(scope) from e
