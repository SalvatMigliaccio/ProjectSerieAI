"""pipeline jobs and workers (ADR 0007, phase 3)

Revision ID: 3f8a1c6e2b71
Revises: 7c1e5a2d9b40
Create Date: 2026-11-18 19:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '3f8a1c6e2b71'
down_revision: str | None = '7c1e5a2d9b40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'pipeline_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('command', sa.String(length=32), nullable=False),
        sa.Column('options', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column('status', sa.String(length=16), nullable=False, server_default='queued'),
        sa.Column('requested_by', sa.Uuid(), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('worker', sa.String(length=128), nullable=True),
        sa.Column('exit_code', sa.Integer(), nullable=True),
        sa.Column('log_tail', sa.Text(), nullable=True),
        sa.CheckConstraint("command IN ('predict_round', 'close_round')",
                           name='ck_pipeline_jobs_command'),
        sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed', 'cancelled')",
                           name='ck_pipeline_jobs_status'),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    # On a constant, not on `status`: an index on `status` would still allow
    # one `queued` row next to one `running` row. At most one active, in all.
    op.execute("CREATE UNIQUE INDEX uq_pipeline_jobs_one_active ON pipeline_jobs ((true)) "
               "WHERE status IN ('queued', 'running')")
    op.create_index('ix_pipeline_jobs_requested_at', 'pipeline_jobs', ['requested_at'])
    op.create_table(
        'pipeline_workers',
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('host', sa.String(length=255), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('poll_seconds', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade() -> None:
    op.drop_table('pipeline_workers')
    op.drop_index('ix_pipeline_jobs_requested_at', table_name='pipeline_jobs')
    op.drop_index('uq_pipeline_jobs_one_active', table_name='pipeline_jobs')
    op.drop_table('pipeline_jobs')
