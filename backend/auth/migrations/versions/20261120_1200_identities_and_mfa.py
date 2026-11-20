"""external identities and second factor (ADR 0008)

Revision ID: a3f8d1c6e2b7
Revises: 3f8a1c6e2b71
Create Date: 2026-11-20 12:00:00

THE DOWNGRADE REFUSES TO LOSE ACCOUNTS. Making `password_hash` NOT NULL again
would fail on any account created through Google, or worse, a downgrade that
deleted them to get through would erase people. It stops instead, and says so.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a3f8d1c6e2b7'
down_revision: str | None = '3f8a1c6e2b71'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('users', 'password_hash', existing_type=sa.Text(), nullable=True)
    op.add_column('users', sa.Column('mfa_method', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('mfa_enabled_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint('ck_users_mfa_method', 'users',
                               "mfa_method IN ('email', 'totp')")
    op.create_check_constraint('ck_users_mfa_consistent', 'users',
                               "(mfa_method IS NULL) = (mfa_enabled_at IS NULL)")
    op.create_check_constraint('ck_users_totp_has_secret', 'users',
                               "mfa_method IS DISTINCT FROM 'totp' OR totp_secret IS NOT NULL")

    op.create_table(
        'user_identities',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('provider', sa.String(length=16), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('email', postgresql.CITEXT(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("provider IN ('google', 'apple')",
                           name='ck_user_identities_provider'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'subject', name='uq_user_identities_subject'),
        sa.UniqueConstraint('user_id', 'provider', name='uq_user_identities_user_provider'),
    )
    op.create_index('ix_user_identities_user_id', 'user_identities', ['user_id'])

    op.create_table(
        'mfa_challenges',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('purpose', sa.String(length=16), nullable=False),
        sa.Column('via', sa.String(length=16), nullable=False),
        sa.Column('challenge_hash', sa.String(length=64), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ip', postgresql.INET(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.CheckConstraint("purpose IN ('sign_in', 'enable', 'disable')",
                           name='ck_mfa_challenges_purpose'),
        sa.CheckConstraint("via IN ('password', 'google', 'apple', 'session')",
                           name='ck_mfa_challenges_via'),
        sa.CheckConstraint('attempts >= 0', name='ck_mfa_challenges_attempts'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('challenge_hash'),
    )
    op.create_index('ix_mfa_challenges_user_id', 'mfa_challenges', ['user_id'])


def downgrade() -> None:
    passwordless = op.get_bind().scalar(
        sa.text("SELECT count(*) FROM users WHERE password_hash IS NULL"))
    if passwordless:
        raise RuntimeError(
            f"{passwordless} account(s) have no password (created through an external "
            f"provider): this downgrade would have to delete them. Stopped."
        )
    op.drop_index('ix_mfa_challenges_user_id', table_name='mfa_challenges')
    op.drop_table('mfa_challenges')
    op.drop_index('ix_user_identities_user_id', table_name='user_identities')
    op.drop_table('user_identities')
    op.drop_constraint('ck_users_totp_has_secret', 'users', type_='check')
    op.drop_constraint('ck_users_mfa_consistent', 'users', type_='check')
    op.drop_constraint('ck_users_mfa_method', 'users', type_='check')
    op.drop_column('users', 'mfa_enabled_at')
    op.drop_column('users', 'mfa_method')
    op.alter_column('users', 'password_hash', existing_type=sa.Text(), nullable=False)
