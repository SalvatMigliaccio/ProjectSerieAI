"""user predictions and favorite team

Revision ID: 7c1e5a2d9b40
Revises: 5079626f3d42
Create Date: 2026-10-21 18:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7c1e5a2d9b40'
down_revision: str | None = '5079626f3d42'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('favorite_team', sa.String(length=64), nullable=True))
    op.create_table(
        'user_predictions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('season', sa.String(length=4), nullable=False),
        sa.Column('matchday', sa.Integer(), nullable=False),
        sa.Column('home_team', sa.String(length=64), nullable=False),
        sa.Column('away_team', sa.String(length=64), nullable=False),
        sa.Column('market', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'season', 'home_team', 'away_team', 'market',
                            name='uq_user_predictions_pick'),
    )
    op.create_index('ix_user_predictions_user_id', 'user_predictions', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_user_predictions_user_id', table_name='user_predictions')
    op.drop_table('user_predictions')
    op.drop_column('users', 'favorite_team')
