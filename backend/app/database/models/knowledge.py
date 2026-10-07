from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IdMixin, TenantScoped, TimestampMixin


class Document(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[str] = mapped_column(String(64), default="policy")  # policy|faq|sop|terms|fees|regulatory...
    product: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="en")
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    access_level: Mapped[str] = mapped_column(String(32), default="public")  # public|customer|internal
    status: Mapped[str] = mapped_column(String(32), default="active")  # active|draft|retired
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tags: Mapped[list] = mapped_column(default=list)


class DocumentVersion(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "document_versions"

    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    version: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(128))
    storage_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|indexed|failed|superseded
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
