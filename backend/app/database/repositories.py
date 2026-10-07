"""Tenant-scoped data access.

`TenantRepository` is the only sanctioned way to read tenant-owned rows: every query it builds carries
`tenant_id = :tenant`, so a caller cannot accidentally (or maliciously) read another tenant's data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Base

M = TypeVar("M", bound=Base)


class TenantIsolationError(PermissionError):
    pass


class TenantRepository(Generic[M]):
    def __init__(self, db: AsyncSession, model: type[M], tenant_id: str) -> None:
        if not tenant_id:
            raise TenantIsolationError("tenant_id is required for tenant-scoped repositories")
        if not hasattr(model, "tenant_id"):
            raise TypeError(f"{model.__name__} is not tenant scoped")
        self.db = db
        self.model = model
        self.tenant_id = tenant_id

    def query(self) -> Select[tuple[M]]:
        return select(self.model).where(self.model.tenant_id == self.tenant_id)  # type: ignore[attr-defined]

    async def get(self, id_: str) -> M | None:
        res = await self.db.execute(self.query().where(self.model.id == id_))  # type: ignore[attr-defined]
        return res.scalar_one_or_none()

    async def get_by(self, **filters: Any) -> M | None:
        q = self.query()
        for k, v in filters.items():
            q = q.where(getattr(self.model, k) == v)
        res = await self.db.execute(q.limit(1))
        return res.scalar_one_or_none()

    async def list(self, *, limit: int = 100, offset: int = 0, order_by: Any = None, **filters: Any) -> Sequence[M]:
        q = self.query()
        for k, v in filters.items():
            q = q.where(getattr(self.model, k) == v)
        if order_by is not None:
            q = q.order_by(order_by)
        res = await self.db.execute(q.limit(limit).offset(offset))
        return res.scalars().all()

    async def add(self, obj: M) -> M:
        existing = getattr(obj, "tenant_id", None)
        if existing and existing != self.tenant_id:
            raise TenantIsolationError("object belongs to a different tenant")
        obj.tenant_id = self.tenant_id  # type: ignore[attr-defined]
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def delete(self, obj: M) -> None:
        if getattr(obj, "tenant_id", None) != self.tenant_id:
            raise TenantIsolationError("object belongs to a different tenant")
        await self.db.delete(obj)
