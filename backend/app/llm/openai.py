"""OpenAI-compatible Chat Completions provider (OpenAI, Azure OpenAI gateways, vLLM, TGI, Ollama, LiteLLM, ...)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.llm.base import (
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMStreamEvent,
    LLMToolCall,
    LLMUsage,
)
from app.observability.tracing import inject_headers


def _to_wire(m: LLMMessage) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        msg["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
            for tc in m.tool_calls
        ]
    if m.role == "tool":
        msg["tool_call_id"] = m.tool_call_id
    return msg


def _parse_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {"_raw": raw}
    except json.JSONDecodeError:
        return {"_raw": raw}  # schema validation downstream rejects this


class OpenAICompatibleProvider(LLMProvider):
    name = "openai"
    supports_json_schema = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 30.0,
        temperature: float = 0.1,
        extra_headers: dict[str, str] | None = None,
        parallel_tool_calls: bool = True,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.parallel_tool_calls = parallel_tool_calls
        headers = {"Content-Type": "application/json", **(extra_headers or {})}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=timeout)

    def _payload(self, messages, tools, tool_choice, temperature, max_tokens, json_schema, stream) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_wire(m) for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "stream": stream,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = [{"type": "function", "function": t.model_dump()} for t in tools]
            body["tool_choice"] = tool_choice or "auto"
            # several calls per turn are fine: the execution engine (not the model) decides what runs in parallel,
            # serializes mutations and policy-checks every step
            body["parallel_tool_calls"] = self.parallel_tool_calls
        if json_schema:
            body["response_format"] = (
                {"type": "json_schema", "json_schema": {"name": json_schema["name"], "schema": json_schema["schema"]}}
                if self.supports_json_schema
                else {"type": "json_object"}
            )
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    async def complete(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None, json_schema=None) -> LLMResponse:
        body = self._payload(messages, tools, tool_choice, temperature, max_tokens, json_schema, stream=False)
        try:
            r = await self._client.post("/chat/completions", json=body, headers=inject_headers({}))
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} request failed: {e}") from e
        data = r.json()
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=[
                LLMToolCall(id=tc["id"], name=tc["function"]["name"], arguments=_parse_args(tc["function"].get("arguments")))
                for tc in msg.get("tool_calls") or []
            ],
            usage=LLMUsage(prompt_tokens=usage.get("prompt_tokens", 0), completion_tokens=usage.get("completion_tokens", 0)),
            finish_reason=choice.get("finish_reason"),
            provider=self.name,
            model=data.get("model", self.model),
        )

    async def stream(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None,
                     channel: str = "unknown") -> AsyncIterator[LLMStreamEvent]:
        body = self._payload(messages, tools, tool_choice, temperature, max_tokens, None, stream=True)
        content: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        usage = LLMUsage()
        finish = None
        try:
            async with self._client.stream("POST", "/chat/completions", json=body, headers=inject_headers({})) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    chunk = json.loads(payload)
                    if u := chunk.get("usage"):
                        usage = LLMUsage(prompt_tokens=u.get("prompt_tokens", 0), completion_tokens=u.get("completion_tokens", 0))
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        finish = choice.get("finish_reason") or finish
                        if text := delta.get("content"):
                            content.append(text)
                            if not calls:  # don't stream narration that accompanies a tool call
                                yield LLMStreamEvent(type="delta", delta=text)
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
        except httpx.HTTPError as e:
            raise LLMError(f"{self.name} stream failed: {e}") from e
        yield LLMStreamEvent(
            type="done",
            response=LLMResponse(
                content="".join(content),
                tool_calls=[
                    LLMToolCall(id=c["id"] or f"call_{i}", name=c["name"], arguments=_parse_args(c["args"]))
                    for i, c in sorted(calls.items())
                ],
                usage=usage,
                finish_reason=finish,
                provider=self.name,
                model=self.model,
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
