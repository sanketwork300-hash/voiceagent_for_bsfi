"""Application configuration. Every setting is overridable through the environment / `.env`."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- application ---
    app_name: str = "bfsi-agent-platform"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool = True
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    auto_seed: bool = False  # seed the demo tenant on startup (dev/demo only)

    # --- datastores ---
    database_url: str = "postgresql+asyncpg://bfsi:bfsi@localhost:5432/bfsi"
    database_echo: bool = False
    db_auto_create: bool = False  # create tables on startup (tests / sqlite quickstart). Otherwise use `alembic upgrade head`
    redis_url: str | None = "redis://localhost:6379/0"  # None -> in-process stores (tests / single node dev)

    # --- security ---
    jwt_secret: str = "change-me-in-production-please-32b"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 3600
    session_token_ttl_seconds: int = 1800
    encryption_key: str | None = None  # Fernet key for integration credentials; derived from jwt_secret if unset
    customer_assertion_secret: str = "demo-bank-idp-shared-secret-32bytes!"  # verifies bank-issued customer assertions
    max_auth_failures: int = 3

    # --- sessions ---
    session_idle_timeout_seconds: int = 1800
    history_window_messages: int = 20
    pending_action_ttl_seconds: int = 300

    # --- LLM ---
    llm_provider: Literal["openai", "local", "rule_based"] = "rule_based"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_fallback_provider: Literal["openai", "local", "rule_based", "none"] = "rule_based"
    llm_timeout_seconds: float = 30.0
    llm_temperature: float = 0.1
    llm_max_tool_iterations: int = 4

    # --- embeddings / RAG ---
    embedding_provider: Literal["openai", "hashing"] = "hashing"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 384
    knowledge_backend: Literal["elasticsearch", "memory"] = "elasticsearch"
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_api_key: str | None = None
    elasticsearch_index: str = "bfsi-knowledge"
    reranker_provider: Literal["lexical", "http", "none"] = "lexical"
    reranker_url: str | None = None  # TEI / Cohere-compatible /rerank endpoint
    rag_top_k: int = 5
    rag_candidate_k: int = 30
    document_storage_dir: str = "./data/uploads"

    # --- voice / LiveKit ---
    livekit_url: str = "ws://localhost:7880"  # used server-side (voice worker, LiveKit API)
    livekit_public_url: str | None = None  # returned to browsers/apps; defaults to livekit_url
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "secret"
    livekit_agent_name: str = "bfsi-voice-agent"
    stt_provider: Literal["deepgram", "sarvam", "openai"] = "deepgram"
    tts_provider: Literal["elevenlabs", "sarvam", "openai"] = "sarvam"
    deepgram_api_key: str | None = None
    sarvam_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    openai_api_key: str | None = None
    voice_min_endpointing_delay: float = 0.5
    voice_max_endpointing_delay: float = 3.0
    voice_min_interruption_duration: float = 0.5
    voice_reconnect_grace_seconds: int = 60
    voice_max_call_seconds: int = 1800
    sip_trunk_id: str | None = None
    sip_human_transfer_uri: str = "sip:contact-centre@pbx.example.com"

    # --- observability ---
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str | None = None  # e.g. http://otel-collector:4318
    otel_service_name: str = "bfsi-agent-backend"

    # --- mock bank (prototype) ---
    mock_bank_url: str = "http://localhost:8100"
    mock_bank_api_key: str = "mock-bank-api-key"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
