"""orchestration: tool execution metadata, durable workflows, step-level tool execution records

Revision ID: 5c1e0a9b7d21
Revises: db1784a7cbcf
Create Date: 2026-10-08 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '5c1e0a9b7d21'
down_revision: str | None = 'db1784a7cbcf'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade() -> None:
    for table in ('api_tools', 'mcp_tools'):
        op.add_column(table, sa.Column('execution', JSON, nullable=False, server_default=sa.text("'{}'")))
    op.add_column('tool_executions', sa.Column('workflow_id', sa.String(length=36), nullable=True))
    op.add_column('tool_executions', sa.Column('step_id', sa.String(length=64), nullable=True))
    op.add_column('tool_executions', sa.Column('idempotency_key', sa.String(length=64), nullable=True))
    op.add_column('tool_executions', sa.Column('failure_category', sa.String(length=32), nullable=True))
    op.create_index('ix_tool_executions_workflow_id', 'tool_executions', ['workflow_id'])
    op.create_index('ix_tool_executions_idempotency_key', 'tool_executions', ['idempotency_key'])
    op.create_table(
        'agent_workflows',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('workflow_type', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('idempotency_key', sa.String(length=64), nullable=False),
        sa.Column('worker_id', sa.String(length=128), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('state', JSON, nullable=False),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_agent_workflows_tenant_id', 'agent_workflows', ['tenant_id'])
    op.create_index('ix_agent_workflows_session_id', 'agent_workflows', ['session_id'])
    op.create_index('ix_agent_workflows_status', 'agent_workflows', ['status'])


def downgrade() -> None:
    op.drop_table('agent_workflows')
    op.drop_index('ix_tool_executions_idempotency_key', 'tool_executions')
    op.drop_index('ix_tool_executions_workflow_id', 'tool_executions')
    for col in ('failure_category', 'idempotency_key', 'step_id', 'workflow_id'):
        op.drop_column('tool_executions', col)
    for table in ('api_tools', 'mcp_tools'):
        op.drop_column(table, 'execution')
