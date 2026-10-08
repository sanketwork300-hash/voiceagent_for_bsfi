"""Composition of planner + executor + verifier + stores, built once per process from Settings."""

from __future__ import annotations

from typing import Any

from app.agents.execution.concurrency import RedisLeaseLimiter, ToolConcurrencyManager
from app.agents.execution.executor import PlanExecutor
from app.agents.execution.models import WorkflowState
from app.agents.execution.planner import ExecutionPlanner
from app.agents.execution.verifier import Verifier
from app.agents.execution.workflow import InflightRegistry, WorkflowStore
from app.config import Settings
from app.database.session import Database
from app.tools.router import ToolGateway


class ExecutionEngine:
    def __init__(self, settings: Settings, db: Database, gateway: ToolGateway, redis_client: Any | None = None) -> None:
        self.settings = settings
        self.store = WorkflowStore(db)
        self.inflight = InflightRegistry()
        distributed = RedisLeaseLimiter(redis_client) if settings.distributed_tool_limits and redis_client is not None else None
        self.concurrency = ToolConcurrencyManager(global_limit=settings.tool_global_concurrency,
                                                  group_limits=settings.tool_group_limits,
                                                  session_limit=settings.max_session_concurrent_tools, distributed=distributed)
        self.verifier = Verifier(attempts=settings.verification_attempts, interval=settings.verification_interval)
        self.planner = ExecutionPlanner(max_steps=settings.max_workflow_steps, max_mutations=settings.max_mutations_per_plan)
        self.executor = PlanExecutor(gateway=gateway, concurrency=self.concurrency, verifier=self.verifier, store=self.store,
                                     inflight=self.inflight, max_parallel=settings.max_parallel_tools,
                                     worker_id=settings.worker_id, slow_ack_seconds=settings.slow_operation_ack_seconds)

    def new_workflow(self, *, tenant_id: str, session_id: str, conversation_id: str, context: dict[str, Any]) -> WorkflowState:
        return WorkflowState(tenant_id=tenant_id, session_id=session_id, conversation_id=conversation_id, context=context,
                             worker_id=self.settings.worker_id)
