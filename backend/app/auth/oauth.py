"""OAuth2 client-credentials for outbound calls to institution APIs (token cached until near expiry)."""

from __future__ import annotations

import asyncio
import time

import httpx


class ClientCredentialsTokenProvider:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._transport = transport

    async def token(self, *, token_url: str, client_id: str, client_secret: str, scope: str | None = None) -> str:
        key = f"{token_url}|{client_id}|{scope}"
        cached = self._tokens.get(key)
        if cached and cached[1] - 30 > time.time():
            return cached[0]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._tokens.get(key)
            if cached and cached[1] - 30 > time.time():
                return cached[0]
            data = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
            if scope:
                data["scope"] = scope
            async with httpx.AsyncClient(timeout=10, transport=self._transport) as c:
                r = await c.post(token_url, data=data)
                r.raise_for_status()
                body = r.json()
            self._tokens[key] = (body["access_token"], time.time() + float(body.get("expires_in", 300)))
            return body["access_token"]
