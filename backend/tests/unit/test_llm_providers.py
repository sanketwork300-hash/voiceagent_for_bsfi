import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm.base import LLMError, LLMMessage, LLMToolSpec
from app.llm.openai import OpenAICompatibleProvider
from app.llm.provider import ResilientLLMProvider
from app.llm.rule_based import RuleBasedLLMProvider

TOOLS = [LLMToolSpec(name="get_loan_details", description="loans", parameters={"type": "object", "properties": {}})]


def provider(handler) -> OpenAICompatibleProvider:
    p = OpenAICompatibleProvider(base_url="http://llm/v1", api_key="k", model="m")
    p._client = httpx.AsyncClient(base_url="http://llm/v1", transport=httpx.MockTransport(handler))
    return p


async def test_complete_parses_tool_calls_and_sends_tools():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"model": "m", "usage": {"prompt_tokens": 10, "completion_tokens": 3}, "choices": [{
            "finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "get_loan_details", "arguments": "{}"}}]}}]})

    r = await provider(handler).complete([LLMMessage(role="user", content="loan?")], tools=TOOLS)
    assert r.tool_calls[0].name == "get_loan_details" and r.usage.prompt_tokens == 10
    assert seen["tools"][0]["function"]["name"] == "get_loan_details" and seen["parallel_tool_calls"] is False


async def test_stream_assembles_fragmented_tool_call_and_suppresses_narration():
    chunks = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "transfer_", "arguments": "{\"amo"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "money", "arguments": "unt\": 5}"}}]}}]},
        {"choices": [{"delta": {"content": "ignored narration"}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    events = [e async for e in provider(lambda r: httpx.Response(200, text=body)).stream([LLMMessage(role="user", content="x")])]
    assert not [e for e in events if e.type == "delta"]
    final = events[-1].response
    assert final.tool_calls[0].name == "transfer_money" and final.tool_calls[0].arguments == {"amount": 5}


async def test_stream_text_deltas():
    body = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': w}}]})}\n\n" for w in ("Hel", "lo")) + "data: [DONE]\n\n"
    events = [e async for e in provider(lambda r: httpx.Response(200, text=body)).stream([LLMMessage(role="user", content="x")])]
    assert "".join(e.delta for e in events if e.type == "delta") == "Hello" and events[-1].response.content == "Hello"


class Out(BaseModel):
    intent: str


async def test_structured_output_uses_json_schema_and_retries():
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        content = "not json" if len(calls) == 1 else '{"intent": "KNOWLEDGE_QUERY"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    out = await provider(handler).structured([LLMMessage(role="user", content="x")], Out)
    assert out.intent == "KNOWLEDGE_QUERY" and calls[0]["response_format"]["type"] == "json_schema" and len(calls) == 2


async def test_malformed_tool_arguments_are_contained():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "t", "arguments": "{not json"}}]}}]})

    r = await provider(handler).complete([LLMMessage(role="user", content="x")])
    assert r.tool_calls[0].arguments == {"_raw": "{not json"}  # rejected later by schema validation


async def test_failover_to_fallback_before_first_token():
    down = provider(lambda r: httpx.Response(503))
    rp = ResilientLLMProvider(down, RuleBasedLLMProvider())
    r = await rp.complete([LLMMessage(role="system", content="RESPONSE_LANGUAGE: en"), LLMMessage(role="user", content="hello")])
    assert r.provider == "rule_based"
    with pytest.raises(LLMError):
        await ResilientLLMProvider(down).complete([LLMMessage(role="user", content="x")])
