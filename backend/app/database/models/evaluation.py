from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, IdMixin, TenantScoped, TimestampMixin


class EvaluationScenario(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "evaluation_scenarios"

    key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(32))  # functional|security
    definition: Mapped[dict] = mapped_column(default=dict)


class EvaluationRun(IdMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "evaluation_runs"

    channels: Mapped[list] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    metrics: Mapped[dict] = mapped_column(default=dict)
    results: Mapped[list] = mapped_column(default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
