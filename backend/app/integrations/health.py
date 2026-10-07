"""Periodic integration health checks (feeds /ready and integration status)."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.database.models import Integration
from app.database.session import Database
from app.integrations.manager import IntegrationManager

log = logging.getLogger(__name__)


class IntegrationHealthMonitor:
    def __init__(self, db: Database, manager: IntegrationManager, interval_seconds: float = 60) -> None:
        self.db = db
        self.manager = manager
        self.interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def check_all(self) -> dict[str, bool]:
        async with self.db.session() as s:
            rows = (await s.execute(select(Integration).where(Integration.is_enabled.is_(True)))).scalars().all()
        results = {}
        for r in rows:
            results[r.id] = (await self.manager.test(r.tenant_id, r.id))["ok"]
        return results

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_all()
            except Exception:
                log.exception("integration health check failed")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
