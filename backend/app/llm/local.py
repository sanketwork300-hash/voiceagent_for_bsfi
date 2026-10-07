"""Self-hosted / on-prem models served behind an OpenAI-compatible endpoint (vLLM, TGI, Ollama, llama.cpp).

Many regulated institutions require inference to stay inside their own network; this provider targets
those deployments. It differs from the hosted provider only in capability flags.
"""

from __future__ import annotations

from app.llm.openai import OpenAICompatibleProvider


class LocalLLMProvider(OpenAICompatibleProvider):
    name = "local"
    supports_json_schema = False  # most local servers support json_object but not full json_schema

    def __init__(self, *, base_url: str = "http://localhost:8000/v1", api_key: str | None = None, model: str, **kw) -> None:
        super().__init__(base_url=base_url, api_key=api_key or "local", model=model, **kw)
