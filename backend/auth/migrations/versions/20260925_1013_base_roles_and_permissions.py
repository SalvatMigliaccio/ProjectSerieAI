"""base roles and permissions

Revision ID: 5079626f3d42
Revises: 264b41a3193f
Create Date: 2026-09-25 10:13:37.466744
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '5079626f3d42'
down_revision: str | None = '264b41a3193f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The catalogue of permissions and the three base roles.
#
# WHY A MIGRATION AND NOT A SCRIPT RUN BY HAND. A fresh database must be
# usable straight after `alembic upgrade head`. A seeding step that lives
# outside migrations is a step somebody skips, and the symptom is
# `LookupError: role 'customer' does not exist` during the first signup —
# which reads like a bug in the code rather than a missing setup step.
#
# WHY IT IS IDEMPOTENT. `ON CONFLICT DO NOTHING` so re-running against a
# database that already holds these rows is harmless.

PERMISSIONS = [
    ("data:read", "Read the public dataset: results, standings, track record"),
    ("picks:read", "Read the model selections"),
    ("users:manage", "Create, suspend and assign roles to users"),
    ("system:manage", "Full control, including system roles"),
]

# customer is what a public signup gets. superadmin is granted by CLI only,
# never through the web, so a signup can never escalate into one.
ROLES = [
    ("customer", "Paying customer", False, ["data:read", "picks:read"]),
    ("admin", "Staff: user administration", False,
     ["data:read", "picks:read", "users:manage"]),
    ("superadmin", "Full control", True,
     ["data:read", "picks:read", "users:manage", "system:manage"]),
]


def upgrade() -> None:
    conn = op.get_bind()
    for name, description in PERMISSIONS:
        conn.execute(
            sa.text("""
                INSERT INTO permissions (id, name, description)
                VALUES (gen_random_uuid(), :name, :description)
                ON CONFLICT (name) DO NOTHING
            """),
            {"name": name, "description": description},
        )

    for name, description, is_system, permissions in ROLES:
        conn.execute(
            sa.text("""
                INSERT INTO roles (id, name, description, is_system)
                VALUES (gen_random_uuid(), :name, :description, :is_system)
                ON CONFLICT (name) DO NOTHING
            """),
            {"name": name, "description": description, "is_system": is_system},
        )
        conn.execute(
            sa.text("""
                INSERT INTO role_permissions (role_id, permission_id)
                SELECT r.id, p.id
                  FROM roles r, permissions p
                 WHERE r.name = :role AND p.name = ANY(:permissions)
                ON CONFLICT DO NOTHING
            """),
            {"role": name, "permissions": permissions},
        )


def downgrade() -> None:
    conn = op.get_bind()
    # role_permissions goes first: the foreign keys cascade anyway, but being
    # explicit means this reads correctly if the cascade is ever removed.
    conn.execute(sa.text("DELETE FROM role_permissions"))
    conn.execute(
        sa.text("DELETE FROM roles WHERE name = ANY(:names)"),
        {"names": [r[0] for r in ROLES]},
    )
    conn.execute(
        sa.text("DELETE FROM permissions WHERE name = ANY(:names)"),
        {"names": [p[0] for p in PERMISSIONS]},
    )
