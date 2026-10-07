"""Provider-neutral LLM contract. Nothing outside app/llm knows which vendor is behind it."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

T = TypeVar("T", bound=BaseModel)


class LLMToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


class LLMToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMResponse(BaseModel):
    content: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    finish_reason: str | None = None
    provider: str = ""
    model: str = ""


class LLMStreamEvent(BaseModel):
    type: Literal["delta", "done"]
    delta: str = ""
    response: LLMResponse | None = None


class LLMError(RuntimeError):
    pass


class StructuredOutputError(LLMError):
    pass


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(text or "")
        if not m:
            raise
        return json.loads(m.group(0))


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[LLMToolSpec] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse: ...

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        tools: list[LLMToolSpec] | None = None,
        tool_choice: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        channel: str = "unknown",  # telemetry label only; providers must not change behaviour on it
    ) -> AsyncIterator[LLMStreamEvent]:
        """Default: non-streaming completion chunked word-by-word. Providers override with real streaming."""
        resp = await self.complete(messages, tools=tools, tool_choice=tool_choice, temperature=temperature, max_tokens=max_tokens)
        if resp.content and not resp.tool_calls:
            for piece in re.findall(r"\S+\s*", resp.content):
                yield LLMStreamEvent(type="delta", delta=piece)
        yield LLMStreamEvent(type="done", response=resp)

    async def structured(self, messages: list[LLMMessage], schema: type[T], *, retries: int = 1) -> T:
        """Structured output validated against a Pydantic model (JSON-schema mode where supported)."""
        json_schema = schema.model_json_schema()
        msgs = list(messages)
        last_err: Exception | None = None
        for _ in range(retries + 1):
            resp = await self.complete(msgs, json_schema={"name": schema.__name__, "schema": json_schema}, temperature=0)
            try:
                return schema.model_validate(parse_json_object(resp.content))
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = e
                msgs = [*msgs, LLMMessage(role="assistant", content=resp.content),
                        LLMMessage(role="user", content=f"Invalid output ({type(e).__name__}). Reply with only JSON matching the schema.")]
        raise StructuredOutputError(str(last_err))

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass
