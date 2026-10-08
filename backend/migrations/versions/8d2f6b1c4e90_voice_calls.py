"""voice_calls: per-call metadata for LiveKit SIP / WebRTC calls (no raw phone numbers)

Revision ID: 8d2f6b1c4e90
Revises: 5c1e0a9b7d21
Create Date: 2026-10-08 18:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '8d2f6b1c4e90'
down_revision: str | None = '5c1e0a9b7d21'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'voice_calls',
        sa.Column('id', sa.String(length=36), primary_key=True),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('conversation_id', sa.String(length=36), nullable=True),
        sa.Column('transport', sa.String(length=16), nullable=False),
        sa.Column('direction', sa.String(length=16), nullable=False),
        sa.Column('room_name', sa.String(length=255), nullable=True),
        sa.Column('participant_identity', sa.String(length=255), nullable=True),
        sa.Column('sip_call_id', sa.String(length=128), nullable=True),
        sa.Column('sip_trunk_id', sa.String(length=64), nullable=True),
        sa.Column('sip_rule_id', sa.String(length=64), nullable=True),
        sa.Column('dialed_number', sa.String(length=32), nullable=True),
        sa.Column('caller_number_masked', sa.String(length=32), nullable=True),
        sa.Column('caller_ref', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('end_reason', sa.String(length=64), nullable=True),
        sa.Column('worker_id', sa.String(length=128), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    for col in ('tenant_id', 'session_id', 'sip_call_id', 'caller_ref', 'status'):
        op.create_index(f'ix_voice_calls_{col}', 'voice_calls', [col])


def downgrade() -> None:
    op.drop_table('voice_calls')
