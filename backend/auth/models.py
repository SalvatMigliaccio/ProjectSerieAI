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
    UniqueConstraint,
    func,
    text,
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

    # The team the user follows, as the track record names it ("Napoli").
    # A plain string and not a foreign key: teams are not a table here, they
    # are names in the match files, and the route checks the name against the
    # season's fixtures before saving it.
    favorite_team: Mapped[str | None] = mapped_column(String(64))

    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(TimestampTz)

    created_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now(), onupdate=now)

    # The join is spelled out because `user_roles` holds TWO foreign keys to
    # `users`: `user_id` (who holds the role) and `granted_by` (who gave it).
    # SQLAlchemy cannot guess which one to walk and raises
    # AmbiguousForeignKeysError at mapper configuration — at import time, far
    # from anything that looks related.
    roles: Mapped[list[Role]] = relationship(
        secondary="user_roles",
        primaryjoin="User.id == UserRole.user_id",
        secondaryjoin="Role.id == UserRole.role_id",
        back_populates="users", lazy="selectin")

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
        secondary="user_roles",
        primaryjoin="Role.id == UserRole.role_id",
        secondaryjoin="User.id == UserRole.user_id",
        back_populates="roles")


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


class UserPrediction(Base):
    """
    A user's own pick on one market of one match ("My Prediction").

    THE SAME RULE AS THE TRACK RECORD, FOR THE SAME REASON. A pick is worth
    comparing with the model only if it was made before kick-off: the route
    refuses to create or delete one after it, server-side, against the
    kickoff in the match files — never against a time the browser sends.
    These rows live in Postgres with the accounts; the track record stays
    files written by local processes, and nothing here touches it.

    The match is named by the project's join key, (season, home, away) — rule
    5: never the date. `matchday` is kept to group the history.
    """

    __tablename__ = "user_predictions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    season: Mapped[str] = mapped_column(String(4), nullable=False)
    matchday: Mapped[int] = mapped_column(Integer, nullable=False)
    home_team: Mapped[str] = mapped_column(String(64), nullable=False)
    away_team: Mapped[str] = mapped_column(String(64), nullable=False)
    # A key of `models.baseline.all_markets` ("1", "over 2.5", "gol-gol"...).
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())

    __table_args__ = (
        # One pick per market per match: picking "over 2.5" twice is not two
        # opinions, and a double row would count twice in the summary.
        UniqueConstraint("user_id", "season", "home_team", "away_team", "market",
                         name="uq_user_predictions_pick"),
    )


class PipelineJob(Base):
    """
    A request to run `predict_round` or `close_round` (ADR 0007, phase 3).

    THE API WRITES THE REQUEST, NEVER THE RESULT. A button in the admin panel
    inserts a `queued` row; a worker on the pipeline machine claims it, runs
    the same command the scheduler runs, and writes back how it went. Nothing
    here can say WHAT the predictions are: the command computes them, and the
    track record's own rules (append-only, kickoff in the future, server
    clock) decide whether they are accepted.

    `command` is a name from a closed list, not a command line: the worker maps
    it to a fixed argv. A string from the database is never executed.
    """

    __tablename__ = "pipeline_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    command: Mapped[str] = mapped_column(String(32), nullable=False)
    # Only flags from a fixed list (`dry_run`, `skip_ingest`): validated by the
    # route on the way in and by the worker on the way out.
    options: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))
    requested_at: Mapped[dt.datetime] = mapped_column(
        TimestampTz, nullable=False, server_default=func.now())
    started_at: Mapped[dt.datetime | None] = mapped_column(TimestampTz)
    finished_at: Mapped[dt.datetime | None] = mapped_column(TimestampTz)
    # Moved by the worker while it runs: a `running` job whose heartbeat is
    # old belongs to a worker that died, and the panel says so.
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(TimestampTz)
    worker: Mapped[str | None] = mapped_column(String(128))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    # The last part of the output, refreshed while it runs. The full log stays
    # on the worker's disk, in logs/.
    log_tail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("command IN ('predict_round', 'close_round')",
                        name="ck_pipeline_jobs_command"),
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'cancelled')",
            name="ck_pipeline_jobs_status"),
        # ONE ACTIVE JOB AT A TIME, enforced by the database: two clicks, two
        # tabs or two admins cannot queue predict_round twice, and predict and
        # close never run together on the same files.
        # On a constant, not on `status`, which would allow one queued AND one
        # running row. The migration creates it with the same expression.
        Index("uq_pipeline_jobs_one_active", text("(true)"),
              unique=True, postgresql_where=text("status IN ('queued', 'running')")),
        Index("ix_pipeline_jobs_requested_at", "requested_at"),
    )


class PipelineWorker(Base):
    """
    Which worker is alive. One row per worker name, refreshed at every poll.

    The panel reads `last_seen_at` to tell "queued, the worker will take it in
    a minute" from "queued, and the PC is off": the second is normal while the
    worker runs on a home computer, and it must be visible, not guessed.
    """

    __tablename__ = "pipeline_workers"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    host: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[dt.datetime] = mapped_column(TimestampTz, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(TimestampTz, nullable=False)
    poll_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
