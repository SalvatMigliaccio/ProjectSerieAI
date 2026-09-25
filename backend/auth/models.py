"""
Authentication tables.

TWO CHOICES WORTH JUSTIFYING, because they look like complications and are not.

1. **Secrets are stored hashed, including the ones that are not passwords.**
   Obvious for passwords; routinely forgotten for the session token and for
   the verification and reset tokens. A database dump — a backup in the wrong
   place, a read-only SQL injection — with tokens in the clear lets an
   attacker impersonate everyone currently signed in without knowing a single
   password. With hashes it does not. The full token exists only in the
   browser cookie and in the emailed link.

2. **Roles are rows, not an enum.** A `users.role` column with three values is
   simpler while there are three. Phase 4 adds subscription tiers, and with an
   enum every new tier is a schema migration; with rows it is an INSERT. The
   price is two join tables.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .ids import uuid7


def now() -> dt.datetime:
    """Explicit UTC. A naive datetime here is a bug you meet six months later."""
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


# Every temporal column is `timestamptz`. With a plain `timestamp`, a server
# that changes TZ silently shifts every deadline already written.
TimestampTz = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)

    # CITEXT rather than String: comparison ignores case, so uniqueness holds
    # on the real address. With String, `Mario@x.it` and `mario@x.it` are two
    # accounts and the user can no longer sign in because they do not remember
    # which spelling they used.
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    # pending -> has not verified the address; active -> may sign in;
    # suspended -> blocked by an admin; deleted -> soft-deleted, because audit
    # events must keep pointing at something afterwards.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")

    email_verified_at: Mapped[dt.datetime | None] = mapped_column(TimestampTz)

    # MFA groundwork: the column exists, the flow does not yet. Adding it later
    # would mean migrating a table holding real accounts.
    totp_secret: Mapped[str | None] = mapped_column(Text)

    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(TimestampTz)

    created_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now(), onupdate=now)

    roles: Mapped[list[Role]] = relationship(
        secondary="user_roles", back_populates="users", lazy="selectin")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'deleted')",
            name="ck_users_status",
        ),
        # The admin panel searches by partial email. Without this index that is
        # a sequential scan on every keystroke.
        Index("ix_users_email_trgm", "email", postgresql_using="gin",
              postgresql_ops={"email": "gin_trgm_ops"}),
    )

    def is_active(self) -> bool:
        return self.status == "active"


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # A system role cannot be deleted from the panel: removing `superadmin`
    # while it is the only role that can grant it locks everyone out, and there
    # is no way back that is not a hand-written query against the database.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    permissions: Mapped[list[Permission]] = relationship(
        secondary="role_permissions", back_populates="roles", lazy="selectin")
    users: Mapped[list[User]] = relationship(
        secondary="user_roles", back_populates="roles")


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Shaped `resource:action`, for example `picks:read`.
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")

    roles: Mapped[list[Role]] = relationship(
        secondary="role_permissions", back_populates="permissions")


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    granted_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())
    # Who granted the role. `SET NULL` rather than `CASCADE`: if the admin is
    # deleted the grant survives, because it is how you reconstruct the way a
    # user came to hold a privilege.
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))


class UserSession(Base):
    """
    A signed-in session.

    Named `UserSession` and not `Session` on purpose: SQLAlchemy's own
    `Session` is imported in every module that touches this one, and two
    things called `Session` in the same file is how the wrong one gets used.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # SHA-256 of the token carried in the cookie. The cleartext never reaches
    # the database: reading this table does not let you impersonate anyone.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())
    # Absolute expiry, fixed at creation: it caps the window of a stolen
    # cookie, which would otherwise stay valid as long as someone uses it.
    expires_at: Mapped[dt.datetime] = mapped_column(TimestampTz, nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(TimestampTz)

    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    user: Mapped[User] = relationship(lazy="joined")

    def is_valid(self, at: dt.datetime, idle: dt.timedelta) -> bool:
        if self.revoked_at is not None:
            return False
        return at < self.expires_at and at - self.last_seen_at < idle


class EmailToken(Base):
    """Address-verification and password-reset tokens."""

    __tablename__ = "email_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)   # verify | reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column(TimestampTz, nullable=False)
    # A table row rather than a self-contained signed token: a signed token
    # cannot be made single-use without tracking who already spent it, and a
    # replayable reset link is a second way into the account that sits in a
    # mailbox forever.
    used_at: Mapped[dt.datetime | None] = mapped_column(TimestampTz)

    __table_args__ = (
        CheckConstraint("kind IN ('verify', 'reset')", name="ck_email_tokens_kind"),
        Index("ix_email_tokens_user_kind", "user_id", "kind"),
    )

    def is_spendable(self, at: dt.datetime) -> bool:
        return self.used_at is None and at < self.expires_at


class AuthEvent(Base):
    """
    The authentication audit log: append-only.

    Same principle as the predictions log — you write, you do not rewrite. A
    failed sign-in that disappears is an attack nobody can reconstruct.
    """

    __tablename__ = "auth_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # NULL when the event concerns an address that does not exist. It is still
    # recorded, because that is the signature of account enumeration.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)   # ok | ko
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    # Never credentials in here: context, not secrets.
    details: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint("outcome IN ('ok', 'ko')", name="ck_auth_events_outcome"),
        Index("ix_auth_events_kind_at", "kind", "at"),
    )
