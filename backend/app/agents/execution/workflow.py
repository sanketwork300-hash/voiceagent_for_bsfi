"""Durable workflow state.

Workflows that contain a state-changing step or a hold (auth / confirmation / approval) are written to the
`agent_workflows` table — before a mutation is sent (write-ahead SUBMITTING), after it returns, and on every hold — so
any worker can resume or reconcile them after a restart. Read-only workflows stay in memory (nothing to recover; they
would only add latency to every turn).

Saves are optimistic: `version` must match, so two workers can never both advance the same workflow.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict

from sqlalchemy import select, update

from app.agents.execution.models import ACTIVE_WORKFLOW, WorkflowState
from app.database.models import AgentWorkflow
from app.database.session import Database
from app.domain import utcnow

log = logging.getLogger(__name__)


class WorkflowConflict(RuntimeError):
    pass


class WorkflowStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def save(self, wf: WorkflowState) -> None:
        if not wf.persistent:
            return
        async with self._locks[wf.workflow_id]:  # executor + shielded write tasks may save concurrently
            wf.updated_at = utcnow()
            payload = json.loads(wf.model_dump_json())
            async with self.db.session() as s:
                if wf.version == 0:
                    s.add(AgentWorkflow(id=wf.workflow_id, tenant_id=wf.tenant_id, session_id=wf.session_id,
                                        conversation_id=wf.conversation_id, workflow_type=wf.workflow_type,
                                        status=wf.status.value, idempotency_key=wf.idempotency_key, worker_id=wf.worker_id,
                                        version=1, state={**payload, "version": 1}, deadline_at=wf.deadline))
                    wf.version = 1
                    return
                new_version = wf.version + 1
                res = await s.execute(
                    update(AgentWorkflow)
                    .where(AgentWorkflow.id == wf.workflow_id, AgentWorkflow.tenant_id == wf.tenant_id,
                           AgentWorkflow.version == wf.version)
                    .values(status=wf.status.value, version=new_version, worker_id=wf.worker_id,
                            state={**payload, "version": new_version}, deadline_at=wf.deadline, updated_at=wf.updated_at))
                if res.rowcount != 1:
                    raise WorkflowConflict(wf.workflow_id)
                wf.version = new_version
        if wf.status not in ACTIVE_WORKFLOW:
            self._locks.pop(wf.workflow_id, None)

    async def get(self, tenant_id: str, workflow_id: str) -> WorkflowState | None:
        async with self.db.session() as s:
            row = (await s.execute(select(AgentWorkflow).where(
                AgentWorkflow.tenant_id == tenant_id, AgentWorkflow.id == workflow_id))).scalar_one_or_none()
        if row is None:
            return None
        wf = WorkflowState.model_validate({k: v for k, v in row.state.items()
                                           if k not in ("current_step", "completed_steps", "failed_steps", "pending_steps")})
        wf.version, wf.persistent = row.version, True
        return wf


class InflightRegistry:
    """Mutation submissions running in this process (shielded from barge-in cancellation), per session."""

    def __init__(self) -> None:
        self._tasks: dict[str, set[asyncio.Task]] = defaultdict(set)

    def add(self, session_id: str, task: asyncio.Task) -> None:
        self._tasks[session_id].add(task)
        task.add_done_callback(lambda t: self._discard(session_id, t))

    def _discard(self, session_id: str, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:  # retrieved here: its awaiter may be gone (barge-in)
            log.error("in-flight submission failed", exc_info=task.exception())
        tasks = self._tasks.get(session_id)
        if tasks is not None:
            tasks.discard(task)
            if not tasks:
                self._tasks.pop(session_id, None)

    def pending(self, session_id: str) -> list[asyncio.Task]:
        return [t for t in self._tasks.get(session_id, ()) if not t.done()]

    async def drain(self, session_id: str, timeout: float) -> bool:
        """Wait (bounded) for in-flight writes of this session, e.g. before releasing its lock after a barge-in."""
        tasks = self.pending(session_id)
        if not tasks:
            return True
        _, still = await asyncio.wait(tasks, timeout=timeout)
        return not still
