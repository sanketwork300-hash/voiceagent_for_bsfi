"""Executes REST / OpenAPI-defined tools against an institution's API."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote

import httpx

from app.integrations.credentials import CredentialManager
from app.observability.tracing import inject_headers
from app.tools.schemas import ToolContext, ToolDefinition, ToolExecutionError

_PATH_PARAM = re.compile(r"\{(\w+)\}")


class RestToolExecutor:
    def __init__(self, credentials: CredentialManager, transport: httpx.AsyncBaseTransport | None = None,
                 max_retries: int = 2) -> None:
        self.credentials = credentials
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(transport=transport, timeout=15, follow_redirects=False)

    async def execute(self, tool: ToolDefinition, args: dict[str, Any], ctx: ToolContext) -> Any:
        if not tool.integration_id:
            raise ToolExecutionError("REST tool has no integration")
        cfg = await self.credentials.get(ctx.tenant_id, tool.integration_id)
        if not cfg.enabled:
            raise ToolExecutionError("integration is disabled")
        b = tool.binding
        method = b.get("method", "GET").upper()
        pmap: dict[str, str] = b.get("parameter_map", {})
        path = b["path"]
        for name in _PATH_PARAM.findall(path):
            if args.get(name) in (None, ""):
                raise ToolExecutionError(f"missing path parameter {name}")
            path = path.replace("{" + name + "}", quote(str(args[name]), safe=""))
        query = {k: v for k, v in args.items() if pmap.get(k) == "query" and v is not None}
        headers = {k: str(v) for k, v in args.items() if pmap.get(k) == "header" and v is not None}
        body = {k: v for k, v in args.items() if pmap.get(k, "body" if method != "GET" else "query") == "body"}
        if method == "GET":
            query.update({k: v for k, v in args.items() if k not in pmap and k not in _PATH_PARAM.findall(b["path"])})
        headers.update(cfg.config.get("headers", {}))
        headers.update(await self.credentials.auth_headers(cfg))
        headers.update({"X-Request-ID": ctx.request_id or "", "X-Channel": ctx.channel.value,
                        "X-Tenant-ID": ctx.tenant_id, "Idempotency-Key": f"{ctx.session_id}:{ctx.request_id}:{tool.name}"})
        inject_headers(headers)
        url = (cfg.base_url or "").rstrip("/") + path
        attempts = 1 + (self.max_retries if tool.idempotent else 0)
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                r = await self._client.request(method, url, params=query or None, json=body if method != "GET" else None,
                                               headers=headers, timeout=tool.timeout_seconds)
            except httpx.TimeoutException as e:
                last = ToolExecutionError("institution API timed out", retryable=True)
                last.__cause__ = e
            except httpx.HTTPError as e:
                last = ToolExecutionError("institution API unreachable", retryable=True)
                last.__cause__ = e
            else:
                if r.status_code < 400:
                    return r.json() if r.content else {}
                detail = _detail(r)
                err = ToolExecutionError(detail, retryable=r.status_code >= 500 or r.status_code == 429, status_code=r.status_code)
                if not err.retryable:
                    raise err
                last = err
            if attempt + 1 < attempts:
                await asyncio.sleep(0.2 * (2**attempt))
        raise last or ToolExecutionError("institution API call failed")

    async def health(self, base_url: str, path: str = "/health", headers: dict[str, str] | None = None) -> bool:
        try:
            r = await self._client.get(base_url.rstrip("/") + path, headers=headers, timeout=5)
            return r.status_code < 500
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        d = body.get("detail") or body.get("message") or body.get("error")
        if isinstance(d, dict):
            d = d.get("message") or str(d)
        return str(d)[:300] if d else f"HTTP {r.status_code}"
    except ValueError:
        return f"HTTP {r.status_code}"
