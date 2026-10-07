from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IdMixin, TenantScoped, TimestampMixin


class Integration(IdMixin, TimestampMixin, TenantScoped, Base):
    """A connection to an institution's system (REST/OpenAPI base, MCP server, custom adapter)."""

    __tablename__ = "integrations"

    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))  # rest|openapi|mcp|adapter
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    auth_type: Mapped[str] = mapped_column(String(32), default="none")  # none|api_key|bearer|oauth2_client_credentials|mtls
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(default=dict)  # headers, timeouts, openapi_url, adapter class ...
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # healthy|degraded|down|unknown
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class MCPServer(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "mcp_servers"

    integration_id: Mapped[str | None] = mapped_column(ForeignKey("integrations.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255))
    transport: Mapped[str] = mapped_column(String(32), default="streamable_http")  # streamable_http|stdio
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    command: Mapped[list | None] = mapped_column(nullable=True)  # for stdio transport
    protocol_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    server_info: Mapped[dict] = mapped_column(default=dict)
    last_discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class _GovernedTool:
    """Columns shared by every externally-backed tool: governance metadata the LLM cannot change."""

    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    input_schema: Mapped[dict] = mapped_column(default=dict)
    risk_level: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    min_auth_state: Mapped[str] = mapped_column(String(32), default="FULLY_AUTHENTICATED")
    requires_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    injected_params: Mapped[dict] = mapped_column(default=dict)  # param -> context key (e.g. customer_id)
    intents: Mapped[list] = mapped_column(default=list)
    confirmation_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=10)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)  # never exposed to the LLM (e.g. verify_otp)


class MCPTool(IdMixin, TimestampMixin, TenantScoped, _GovernedTool, Base):
    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_mcp_tool_tenant_name"),)

    server_id: Mapped[str] = mapped_column(ForeignKey("mcp_servers.id"), index=True)
    remote_name: Mapped[str] = mapped_column(String(128))
    annotations: Mapped[dict] = mapped_column(default=dict)


class APITool(IdMixin, TimestampMixin, TenantScoped, _GovernedTool, Base):
    __tablename__ = "api_tools"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_api_tool_tenant_name"),)

    integration_id: Mapped[str] = mapped_column(ForeignKey("integrations.id"), index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(Text)
    operation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parameter_map: Mapped[dict] = mapped_column(default=dict)  # param -> path|query|header|body
    idempotent: Mapped[bool] = mapped_column(Boolean, default=False)
