"""Application configuration. Every setting is overridable through the environment / `.env`."""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
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
    llm_parallel_tool_calls: bool = True  # let the model propose several tool calls per turn (scheduled by the engine)

    # --- orchestration: agent loop + execution engine (limits are configuration, validated below) ---
    worker_id: str = Field(default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}")
    max_agent_iterations: int = Field(4, ge=1, le=12, validation_alias=AliasChoices("max_agent_iterations", "llm_max_tool_iterations"))
    max_workflow_steps: int = Field(12, ge=1, le=64)
    workflow_timeout: float = Field(45.0, gt=0)  # seconds, whole plan execution within one turn
    default_tool_timeout: float = Field(10.0, gt=0)
    max_parallel_tools: int = Field(4, ge=1, le=32)  # independent read steps run at once within one plan wave
    max_session_concurrent_tools: int = Field(4, ge=1, le=32)
    tool_global_concurrency: int = Field(64, ge=1, validation_alias=AliasChoices("tool_global_concurrency", "max_concurrent_tools"))  # per worker process — NOT cluster-wide
    tool_group_limits: dict[str, int] = Field(default_factory=lambda: {"banking_api": 16, "knowledge": 16})
    distributed_tool_limits: bool = False  # also enforce tool_group_limits cluster-wide with Redis leases
    max_mutations_per_plan: int = Field(1, ge=1, le=4)  # state-changing steps one LLM turn may propose
    read_retry_count: int = Field(2, ge=0, le=5)
    write_retry_count: int = Field(1, ge=0, le=3)  # idempotent non-financial writes only; financial: never
    verification_attempts: int = Field(3, ge=1, le=10)
    verification_interval: float = Field(0.5, ge=0)
    slow_operation_ack_seconds: float = Field(1.2, ge=0)  # voice: speak a neutral acknowledgement after this
    session_lock_ttl: float = Field(30.0, ge=5)  # renewed while a turn runs
    session_lock_wait: float = Field(20.0, gt=0)  # how long a concurrent request waits before "busy"
    redis_required_in_production: bool = True

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
    voice_reconnect_grace_seconds: int = 60  # WebRTC only; a phone hangup ends the call immediately
    voice_max_call_seconds: int = 1800
    # --- telephony: LiveKit Phone Numbers / LiveKit SIP (no third-party PSTN provider in the app) ---
    # Inbound: a LiveKit phone number (or a carrier SIP trunk into LiveKit SIP) + a dispatch rule that creates one
    # room per caller and dispatches `livekit_agent_name`. Provision with `python -m scripts.provision_telephony`.
    livekit_sip_room_prefix: str = "call-"
    livekit_sip_dispatch_rule_name: str = "bfsi-inbound"
    livekit_sip_outbound_trunk_id: str | None = Field(None, validation_alias=AliasChoices("livekit_sip_outbound_trunk_id", "sip_trunk_id"))
    sip_human_transfer_uri: str = "sip:contact-centre@pbx.example.com"
    sip_overflow_transfer_uri: str | None = None  # where to send callers when all lines are busy (else: message + hang up)
    caller_id_hash_key: str | None = None
    demo_sip_numbers: list[str] = Field(default_factory=list)  # demo tenant's inbound numbers (seed only)  # HMAC key for the internal caller reference; derived from jwt_secret if unset

    # --- capacity (fail gracefully instead of exhausting resources; see docs/voice.md "Scaling") ---
    max_active_calls: int = Field(200, ge=1)  # cluster-wide when Redis is configured (expiring leases)
    max_agent_sessions_per_worker: int = Field(25, ge=1)  # reported to LiveKit as worker load: full workers get no jobs
    max_concurrent_llm_requests: int = Field(64, ge=1)  # cluster-wide with Redis; over the limit -> fallback provider
    llm_queue_timeout: float = Field(5.0, gt=0)  # max wait for an LLM slot before failing over
    max_concurrent_stt_requests: int = Field(200, ge=1)  # STT streams (one per active call), cluster-wide with Redis
    max_concurrent_tts_requests: int = Field(200, ge=1)  # TTS streams (at most one per active call), cluster-wide

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

    @model_validator(mode="after")
    def _validate_orchestration(self) -> Settings:
        problems = []
        if self.is_production and self.redis_required_in_production and not self.redis_url:
            problems.append("REDIS_URL is required in production: in-process session state/locks are not shared across workers")
        if self.workflow_timeout <= self.default_tool_timeout:
            problems.append("WORKFLOW_TIMEOUT must exceed DEFAULT_TOOL_TIMEOUT")
        if self.session_lock_ttl <= self.default_tool_timeout:
            problems.append("SESSION_LOCK_TTL must exceed DEFAULT_TOOL_TIMEOUT (the lock is renewed, but must outlive one call)")
        if any(v < 1 for v in self.tool_group_limits.values()):
            problems.append("TOOL_GROUP_LIMITS values must be >= 1")
        if self.distributed_tool_limits and not self.redis_url:
            problems.append("DISTRIBUTED_TOOL_LIMITS requires REDIS_URL")
        if problems:
            raise ValueError("; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
