"""MCP transports: Streamable HTTP and stdio, both speaking JSON-RPC 2.0."""

from __future__ import annotations

import asyncio
import itertools
import json
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.observability.tracing import inject_headers

PROTOCOL_VERSION = "2025-06-18"


class MCPTransportError(RuntimeError):
    def __init__(self, message: str, *, category: str = "NETWORK_ERROR", sent: bool | None = None) -> None:
        super().__init__(message)
        self.category = category  # app.tools.failures.FailureCategory value
        self.sent = sent


class MCPTransport(ABC):
    def __init__(self) -> None:
        self._ids = itertools.count(1)

    def next_id(self) -> int:
        return next(self._ids)

    @abstractmethod
    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 15) -> dict[str, Any]: ...

    @abstractmethod
    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None: ...

    async def aclose(self) -> None:  # noqa: B027
        pass


class StreamableHTTPTransport(MCPTransport):
    def __init__(self, url: str, headers: dict[str, str] | None = None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        super().__init__()
        self.url = url
        self.session_id: str | None = None
        self.protocol_version = PROTOCOL_VERSION
        self._headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", **(headers or {})}
        self._client = httpx.AsyncClient(transport=transport, timeout=30)

    def _h(self) -> dict[str, str]:
        h = dict(self._headers)
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        h["MCP-Protocol-Version"] = self.protocol_version
        return inject_headers(h)

    async def request(self, method, params=None, timeout=15):
        rid = self.next_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        try:
            r = await self._client.post(self.url, json=msg, headers=self._h(), timeout=timeout)
        except httpx.ConnectError as e:
            raise MCPTransportError(f"MCP server unreachable: {e}", sent=False) from e
        except httpx.TimeoutException as e:
            raise MCPTransportError("MCP server timed out", category="TIMEOUT") from e
        except httpx.HTTPError as e:
            raise MCPTransportError(f"MCP server unreachable: {e}") from e
        if r.status_code >= 400:
            # 404 = MCP session expired / unknown: the call was not processed
            raise MCPTransportError(f"MCP HTTP {r.status_code}", category="DEPENDENCY_UNAVAILABLE" if r.status_code >= 500 else "NETWORK_ERROR",
                                    sent=False if r.status_code in (401, 404) else None)
        if sid := r.headers.get("mcp-session-id"):
            self.session_id = sid
        ctype = r.headers.get("content-type", "")
        if ctype.startswith("text/event-stream"):
            for block in r.text.split("\n\n"):
                data = "".join(ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:"))
                if data:
                    obj = json.loads(data)
                    if obj.get("id") == rid:
                        return _unwrap(obj)
            raise MCPTransportError("no response in event stream")
        return _unwrap(r.json())

    async def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        try:
            await self._client.post(self.url, json=msg, headers=self._h(), timeout=10)
        except httpx.HTTPError as e:
            raise MCPTransportError(str(e)) from e

    async def aclose(self) -> None:
        if self.session_id:
            try:
                await self._client.delete(self.url, headers=self._h(), timeout=5)
            except httpx.HTTPError:
                pass
        await self._client.aclose()


class StdioTransport(MCPTransport):
    """Spawns a local MCP server process (e.g. an institution-provided connector binary)."""

    def __init__(self, command: list[str], env: dict[str, str] | None = None) -> None:
        super().__init__()
        self.command = command
        self.env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> asyncio.subprocess.Process:
        if self._proc and self._proc.returncode is None:
            return self._proc
        self._proc = await asyncio.create_subprocess_exec(
            *self.command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=self.env)
        self._reader = asyncio.create_task(self._read_loop(self._proc))
        return self._proc

    async def _read_loop(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout
        while line := await proc.stdout.readline():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            fut = self._pending.pop(obj.get("id"), None)
            if fut and not fut.done():
                fut.set_result(obj)

    async def _send(self, msg: dict[str, Any]) -> None:
        proc = await self._ensure()
        assert proc.stdin
        async with self._lock:
            proc.stdin.write((json.dumps(msg) + "\n").encode())
            await proc.stdin.drain()

    async def request(self, method, params=None, timeout=15):
        rid = self.next_id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        try:
            return _unwrap(await asyncio.wait_for(fut, timeout))
        except TimeoutError as e:
            self._pending.pop(rid, None)
            raise MCPTransportError(f"MCP stdio timeout on {method}", category="TIMEOUT") from e

    async def notify(self, method, params=None):
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def aclose(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
        if self._reader:
            self._reader.cancel()


def _unwrap(obj: dict[str, Any]) -> dict[str, Any]:
    if "error" in obj:
        err = obj["error"]
        raise MCPTransportError(f"MCP error {err.get('code')}: {err.get('message')}")
    return obj.get("result") or {}
