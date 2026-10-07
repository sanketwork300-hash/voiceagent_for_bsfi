from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IdMixin, TenantScoped, TimestampMixin


class Tenant(IdMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    slug: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    institution_type: Mapped[str] = mapped_column(String(32), default="bank")  # bank|nbfc|insurer|fintech|...
    default_language: Mapped[str] = mapped_column(String(8), default="en")
    supported_languages: Mapped[list] = mapped_column(default=lambda: ["en", "hi"])
    settings: Mapped[dict] = mapped_column(default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(IdMixin, TimestampMixin, TenantScoped, Base):
    """Platform/staff users (tenant admins, human agents, auditors). Bank customers are NOT stored here:
    the bank remains the system of record; we only hold its opaque customer reference in sessions."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    roles: Mapped[list] = mapped_column(default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Agent(IdMixin, TimestampMixin, TenantScoped, Base):
    """A configured assistant persona. The same agent serves chat and voice."""

    __tablename__ = "agents"

    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    persona_prompt: Mapped[str] = mapped_column(Text, default="")
    allowed_tools: Mapped[list | None] = mapped_column(nullable=True)  # None => all enabled tenant tools
    channels: Mapped[list] = mapped_column(default=lambda: ["chat", "voice"])
    languages: Mapped[list] = mapped_column(default=lambda: ["en", "hi"])
    voice_config: Mapped[dict] = mapped_column(default=dict)  # stt/tts voices per language
    llm_config: Mapped[dict] = mapped_column(default=dict)  # model overrides
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
