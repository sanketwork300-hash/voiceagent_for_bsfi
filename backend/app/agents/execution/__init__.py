"""Agent execution engine: Reason -> Plan -> Policy -> Act -> Verify for one agent turn.

    planner.py      tool calls proposed by the model -> validated DAG (dedupe, templates, ordering, plan policy)
    executor.py     dependency-ordered waves; parallel reads, serialized mutations, write-ahead, shielded submits
    verifier.py     read-only outcome checks (status lookup by idempotency key / state check)
    concurrency.py  global / group / tool / session limits (in-process) + optional Redis leases (cluster-wide)
    workflow.py     durable WorkflowState (agent_workflows table) and in-flight submission registry
    templates.py    trusted server-side workflow templates (e.g. transfer: resolve beneficiary -> validate -> transfer -> verify)

The orchestrator (app.agents.orchestrator) owns the conversation; this package owns *execution*. The LLM proposes,
the policy engine authorises, this engine schedules and verifies — the LLM is never the workflow engine.
"""

from app.agents.execution.engine import ExecutionEngine

__all__ = ["ExecutionEngine"]
