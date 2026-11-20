"""authenticator-app second factor: replay guard and recovery codes (ADR 0008)

Revision ID: c5e2a9f4b1d3
Revises: a3f8d1c6e2b7
Create Date: 2026-11-20 17:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c5e2a9f4b1d3'
down_revision: str | None = 'a3f8d1c6e2b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('totp_last_step', sa.BigInteger(), nullable=True))
    op.create_table(
        'mfa_recovery_codes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'code_hash', name='uq_mfa_recovery_codes_code'),
    )
    op.create_index('ix_mfa_recovery_codes_user_id', 'mfa_recovery_codes', ['user_id'])


def downgrade() -> None:
    totp_users = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM users WHERE mfa_method = 'totp'"))
    if totp_users:
        raise RuntimeError(
            f"{totp_users} account(s) use the authenticator app: this downgrade would "
            f"delete their recovery codes and leave them unable to recover. Stopped."
        )
    op.drop_index('ix_mfa_recovery_codes_user_id', table_name='mfa_recovery_codes')
    op.drop_table('mfa_recovery_codes')
    op.drop_column('users', 'totp_last_step')
