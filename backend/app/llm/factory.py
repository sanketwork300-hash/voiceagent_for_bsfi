from __future__ import annotations

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.provider import LLMCapacity, ResilientLLMProvider


def _build(kind: str, s: Settings) -> LLMProvider | None:
    if kind == "openai":
        from app.llm.openai import OpenAICompatibleProvider

        return OpenAICompatibleProvider(base_url=s.llm_base_url, api_key=s.llm_api_key, model=s.llm_model,
                                        timeout=s.llm_timeout_seconds, temperature=s.llm_temperature,
                                        parallel_tool_calls=s.llm_parallel_tool_calls)
    if kind == "local":
        from app.llm.local import LocalLLMProvider

        return LocalLLMProvider(base_url=s.llm_base_url, api_key=s.llm_api_key, model=s.llm_model,
                                timeout=s.llm_timeout_seconds, temperature=s.llm_temperature,
                                parallel_tool_calls=s.llm_parallel_tool_calls)
    if kind == "rule_based":
        from app.llm.rule_based import RuleBasedLLMProvider

        return RuleBasedLLMProvider()
    return None


def create_llm_provider(settings: Settings, slot_pool: object | None = None) -> ResilientLLMProvider:
    """`slot_pool` (app.agents.execution.concurrency.SlotPool) enforces MAX_CONCURRENT_LLM_REQUESTS on the primary."""
    primary = _build(settings.llm_provider, settings)
    if primary is None:
        raise ValueError(f"unknown llm_provider {settings.llm_provider}")
    fallback = None
    if settings.llm_fallback_provider not in ("none", settings.llm_provider):
        fallback = _build(settings.llm_fallback_provider, settings)
    capacity = None
    if slot_pool is not None and settings.llm_provider != "rule_based":  # the offline stand-in needs no limit
        capacity = LLMCapacity(pool=slot_pool, limit=settings.max_concurrent_llm_requests,
                               queue_timeout=settings.llm_queue_timeout, lease_ttl=settings.llm_timeout_seconds + 5)
    return ResilientLLMProvider(primary, fallback, capacity)
