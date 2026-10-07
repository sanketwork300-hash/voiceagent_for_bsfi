"""Instrumented, failover-capable wrapper used by the runtime around any concrete provider."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from app.llm.base import LLMError, LLMProvider, LLMResponse, LLMStreamEvent
from app.observability import metrics
from app.observability.tracing import span

log = logging.getLogger(__name__)


class ResilientLLMProvider(LLMProvider):
    """Adds tracing, latency/token metrics and failover to a secondary provider.

    Failover only happens before any token has been streamed, so a user never sees two half-answers.
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self.model = primary.model

    def _record(self, provider: LLMProvider, resp: LLMResponse, started: float, purpose: str, channel: str) -> None:
        metrics.llm_latency.labels(channel, provider.name, purpose).observe(time.perf_counter() - started)
        metrics.llm_tokens.labels(channel, provider.name, "prompt").inc(resp.usage.prompt_tokens)
        metrics.llm_tokens.labels(channel, provider.name, "completion").inc(resp.usage.completion_tokens)

    async def complete(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None,
                       json_schema=None, purpose: str = "chat", channel: str = "unknown") -> LLMResponse:
        for provider in filter(None, (self.primary, self.fallback)):
            started = time.perf_counter()
            with span("llm.complete", **{"llm.provider": provider.name, "llm.model": provider.model, "llm.purpose": purpose}):
                try:
                    resp = await provider.complete(messages, tools=tools, tool_choice=tool_choice, temperature=temperature,
                                                   max_tokens=max_tokens, json_schema=json_schema)
                    self._record(provider, resp, started, purpose, channel)
                    return resp
                except LLMError:
                    log.warning("llm provider failed; trying fallback", extra={"provider": provider.name})
                    if provider is self.fallback or self.fallback is None:
                        raise
        raise LLMError("no LLM provider available")

    async def stream(self, messages, *, tools=None, tool_choice=None, temperature=None, max_tokens=None,
                     channel: str = "unknown") -> AsyncIterator[LLMStreamEvent]:
        for provider in filter(None, (self.primary, self.fallback)):
            started = time.perf_counter()
            emitted = False
            try:
                with span("llm.stream", **{"llm.provider": provider.name, "llm.model": provider.model}):
                    async for ev in provider.stream(messages, tools=tools, tool_choice=tool_choice,
                                                    temperature=temperature, max_tokens=max_tokens):
                        if ev.type == "delta":
                            emitted = True
                        elif ev.response is not None:
                            self._record(provider, ev.response, started, "chat", channel)
                        yield ev
                return
            except LLMError:
                if emitted or provider is self.fallback or self.fallback is None:
                    raise
                log.warning("llm stream failed; trying fallback", extra={"provider": provider.name})

    async def structured(self, messages, schema, *, retries: int = 1):
        for provider in filter(None, (self.primary, self.fallback)):
            started = time.perf_counter()
            try:
                with span("llm.structured", **{"llm.provider": provider.name, "schema": schema.__name__}):
                    out = await provider.structured(messages, schema, retries=retries)
                metrics.llm_latency.labels("unknown", provider.name, schema.__name__).observe(time.perf_counter() - started)
                return out
            except LLMError:
                if provider is self.fallback or self.fallback is None:
                    raise
        raise LLMError("no LLM provider available")

    async def aclose(self) -> None:
        for p in filter(None, (self.primary, self.fallback)):
            await p.aclose()
