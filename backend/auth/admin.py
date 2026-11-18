"""
The administration panel: who signed up, find an account, suspend it, give
it back, name the administrators.

THE THIRD WRITABLE PREFIX, AND WHAT IT WRITES. `/api/admin` writes accounts
and roles in Postgres, the same tables `/api/auth` already writes. It does not
touch the track record. Launching `predict_round` and `close_round` from here
is phase 3 of the plan of 18 November 2026: a queued request that a worker
picks up, never a model run inside this process.

THE PERMISSIONS COME FROM THE DATABASE, NOT FROM THE TOKEN. The data routes
trust the access token's snapshot of permissions for its 15 minutes (ADR
0003). An admin route must not: a revoked administrator would keep suspending
accounts for a quarter of an hour. So every route here loads the live session
and the user's roles, as the account routes do, and revocation is immediate.

TWO LEVELS, CHECKED HERE AND NOWHERE ELSE.
- `users:manage` (admin and superadmin): list, search, see an account's
  activity, suspend, reactivate, unlock.
- `system:manage` (superadmin only): grant and revoke `admin`, and act on an
  account that is itself an administrator.

THREE THINGS NO HTTP CALL CAN DO, whoever makes it:
- act on your own account (suspending yourself is a lock-out, not a feature);
- act on a `superadmin` — that role comes from `AI_NAPLES_SUPERADMIN_EMAILS`
  or the CLI, never from a click, so no sequence of requests escalates;
- grant any role except `admin`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import service
from .deps import CurrentUser, Db, client_ip
from .models import AuthEvent, Role, User, UserRole, UserSession, now
from .ratelimit import admin_action_limiter

router = APIRouter(prefix="/api/admin", tags=["admin"])

# The only role a click can hand out. `superadmin` is bootstrap-only;
# `customer` is what signup grants and taking it away is not a use case yet.
GRANTABLE = frozenset({"admin"})

# The events the panel's audit list shows: what staff did, and the account
# changes that explain them. Sign-ins stay in each account's own detail view.
STAFF_KINDS = ("role_granted", "role_revoked", "suspended", "reactivated",
               "unlocked", "account_deleted")


# --- who may call -----------------------------------------------------------

def _staff(user: CurrentUser) -> User:
    if not service.has_permission(user, "users:manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing permission: users:manage")
    return user


def _superadmin(user: CurrentUser) -> User:
    if not service.has_permission(user, "system:manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing permission: system:manage")
    return user


Staff = Annotated[User, Depends(_staff)]
SuperAdmin = Annotated[User, Depends(_superadmin)]


def _throttle(actor: User) -> None:
    key = str(actor.id)
    if not admin_action_limiter.allow(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "too many actions: wait a few minutes",
            headers={"Retry-After": str(admin_action_limiter.retry_after(key))})


def _target(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such user")
    return user


def _role_names(user: User) -> set[str]:
    return {r.name for r in user.roles}


def _may_act_on(actor: User, target: User) -> None:
    """The three refusals of the module docstring that concern the target."""
    if actor.id == target.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "you cannot do this on your own account")
    roles = _role_names(target)
    if "superadmin" in roles:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "a superadmin is managed from AI_NAPLES_SUPERADMIN_EMAILS or the CLI, not from here")
    if "admin" in roles and not service.has_permission(actor, "system:manage"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "only a superadmin can act on an administrator")


# --- answers ----------------------------------------------------------------

def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


class UserRow(BaseModel):
    id: str
    email: str
    status: str
    roles: list[str]
    created_at: str
    email_verified_at: str | None = None
    last_seen_at: str | None = None
    active_sessions: int
    locked: bool


class UserPage(BaseModel):
    total: int
    limit: int
    offset: int
    rows: list[UserRow]


class EventRow(BaseModel):
    """
    An audit row as staff see it: WITH `details`, unlike the owner's view.

    `details` never holds credentials (`record_event` is written that way), and
    the context it does hold — the cause of a refusal, who granted a role — is
    precisely what an administrator needs. `actor_email` resolves the `by` id.
    """

    id: str
    kind: str
    outcome: str
    at: str
    ip: str | None = None
    user_agent: str | None = None
    details: dict | None = None
    user_email: str | None = None
    actor_email: str | None = None


class SessionInfo(BaseModel):
    id: str
    created_at: str
    last_seen_at: str
    expires_at: str
    ip: str | None = None
    user_agent: str | None = None


class UserDetail(BaseModel):
    user: UserRow
    sessions: list[SessionInfo]
    events: list[EventRow]
    # What the CALLER may do on this account, so the interface draws only the
    # buttons that will work. Computed with the same rules the routes enforce.
    can_manage: bool
    can_change_roles: bool


class DayCount(BaseModel):
    day: str
    signups: int


class Summary(BaseModel):
    total: int
    by_status: dict[str, int]
    new_24h: int
    new_7d: int
    new_30d: int
    admins: int
    active_sessions: int
    signups_by_day: list[DayCount]


def _rows(db: Session, users: list[User]) -> list[UserRow]:
    """Last activity and live sessions for a page of users, in one query."""
    if not users:
        return []
    at = now()
    stats = dict(
        (uid, (last, live)) for uid, last, live in db.execute(
            select(
                UserSession.user_id,
                func.max(UserSession.last_seen_at),
                func.count().filter(UserSession.revoked_at.is_(None),
                                    UserSession.expires_at > at),
            )
            .where(UserSession.user_id.in_([u.id for u in users]))
            .group_by(UserSession.user_id)
        ).all()
    )
    out = []
    for u in users:
        last, live = stats.get(u.id, (None, 0))
        out.append(UserRow(
            id=str(u.id), email=u.email, status=u.status,
            roles=sorted(_role_names(u)),
            created_at=u.created_at.isoformat(),
            email_verified_at=_iso(u.email_verified_at),
            last_seen_at=_iso(last),
            active_sessions=live,
            locked=bool(u.locked_until and u.locked_until > at),
        ))
    return out


def _events(db: Session, rows: list[AuthEvent]) -> list[EventRow]:
    """Resolve the target and the acting admin of each event to an address."""
    ids: set[uuid.UUID] = set()
    for e in rows:
        if e.user_id:
            ids.add(e.user_id)
        by = (e.details or {}).get("by")
        if by:
            try:
                ids.add(uuid.UUID(by))
            except ValueError:
                pass
    emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(ids))).all()) if ids else {}

    def email_of(value) -> str | None:
        try:
            return emails.get(uuid.UUID(str(value))) if value else None
        except ValueError:
            return None

    return [EventRow(
        id=str(e.id), kind=e.kind, outcome=e.outcome, at=e.at.isoformat(),
        ip=str(e.ip) if e.ip else None, user_agent=e.user_agent, details=e.details,
        user_email=emails.get(e.user_id) if e.user_id else None,
        actor_email=email_of((e.details or {}).get("by")),
    ) for e in rows]


def _like(text: str) -> str:
    """`%text%` with the LIKE wildcards in the input taken literally."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- reads ------------------------------------------------------------------

@router.get("/summary")
def summary(actor: Staff, db: Db) -> Summary:
    at = now()
    by_status = dict(db.execute(select(User.status, func.count()).group_by(User.status)).all())

    def since(delta: dt.timedelta) -> int:
        return db.scalar(select(func.count()).select_from(User)
                         .where(User.created_at >= at - delta)) or 0

    # Thirty days, the empty ones included: a chart with gaps reads as a
    # smooth trend between two signups a week apart.
    start = (at - dt.timedelta(days=29)).date()
    day = func.date_trunc("day", User.created_at).label("day")
    counts = {d.date(): n for d, n in db.execute(
        select(day, func.count()).where(User.created_at >= dt.datetime.combine(
            start, dt.time(), tzinfo=dt.UTC)).group_by(day)).all()}
    series = [DayCount(day=(start + dt.timedelta(days=i)).isoformat(),
                       signups=counts.get(start + dt.timedelta(days=i), 0))
              for i in range(30)]

    admins = db.scalar(
        select(func.count(func.distinct(UserRole.user_id)))
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.name.in_(("admin", "superadmin")))) or 0
    live = db.scalar(select(func.count()).select_from(UserSession).where(
        UserSession.revoked_at.is_(None), UserSession.expires_at > at)) or 0

    return Summary(
        total=sum(by_status.values()), by_status=by_status,
        new_24h=since(dt.timedelta(hours=24)), new_7d=since(dt.timedelta(days=7)),
        new_30d=since(dt.timedelta(days=30)), admins=admins, active_sessions=live,
        signups_by_day=series)


@router.get("/users")
def users(
    actor: Staff,
    db: Db,
    q: Annotated[str | None, Query(max_length=254)] = None,
    status_: Annotated[str | None, Query(alias="status", pattern="^(pending|active|suspended)$")] = None,
    role: Annotated[str | None, Query(pattern="^(admin|superadmin|customer|staff)$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    """
    Newest first, filtered by partial address, status and role.

    `role=staff` means admin OR superadmin. The search uses the trigram index
    on `users.email`, which is what makes a substring match cheap.
    """
    stmt = select(User)
    if q and q.strip():
        stmt = stmt.where(User.email.ilike(_like(q.strip()), escape="\\"))
    if status_:
        stmt = stmt.where(User.status == status_)
    if role:
        names = ("admin", "superadmin") if role == "staff" else (role,)
        stmt = stmt.where(User.id.in_(
            select(UserRole.user_id).join(Role, Role.id == UserRole.role_id)
            .where(Role.name.in_(names))))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    page = db.scalars(stmt.order_by(User.created_at.desc(), User.id.desc())
                      .limit(limit).offset(offset)).all()
    return UserPage(total=total, limit=limit, offset=offset, rows=_rows(db, list(page)))


def _detail(db: Session, actor: User, target: User) -> UserDetail:
    at = now()
    sessions = db.scalars(select(UserSession).where(
        UserSession.user_id == target.id, UserSession.revoked_at.is_(None),
        UserSession.expires_at > at).order_by(UserSession.last_seen_at.desc())).all()
    events = db.scalars(select(AuthEvent).where(AuthEvent.user_id == target.id)
                        .order_by(AuthEvent.at.desc(), AuthEvent.id.desc()).limit(50)).all()
    try:
        _may_act_on(actor, target)
        can_manage = True
    except HTTPException:
        can_manage = False
    return UserDetail(
        user=_rows(db, [target])[0],
        sessions=[SessionInfo(
            id=str(s.id), created_at=s.created_at.isoformat(),
            last_seen_at=s.last_seen_at.isoformat(), expires_at=s.expires_at.isoformat(),
            ip=str(s.ip) if s.ip else None, user_agent=s.user_agent) for s in sessions],
        events=_events(db, list(events)),
        can_manage=can_manage,
        can_change_roles=can_manage and service.has_permission(actor, "system:manage"),
    )


@router.get("/users/{user_id}")
def user_detail(user_id: uuid.UUID, actor: Staff, db: Db) -> UserDetail:
    return _detail(db, actor, _target(db, user_id))


@router.get("/audit")
def audit(actor: Staff, db: Db,
          limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[EventRow]:
    """What staff did lately, and the deletions, newest first."""
    rows = db.scalars(select(AuthEvent).where(AuthEvent.kind.in_(STAFF_KINDS))
                      .order_by(AuthEvent.at.desc(), AuthEvent.id.desc()).limit(limit)).all()
    return _events(db, list(rows))


# --- writes -----------------------------------------------------------------

class Reason(BaseModel):
    # Optional, short, and shown in the audit list. Never sent to the user.
    reason: str | None = Field(default=None, max_length=500)


def _audit(db: Session, request: Request, kind: str, actor: User, target: User,
           **details) -> None:
    service.record_event(db, kind, "ok", user_id=target.id, ip=client_ip(request),
                         user_agent=request.headers.get("user-agent"),
                         details={"by": str(actor.id), **details})


@router.post("/users/{user_id}/suspend")
def suspend(user_id: uuid.UUID, payload: Reason, request: Request,
            actor: Staff, db: Db) -> UserDetail:
    """
    Block the account and end every session it holds.

    WHAT IT DOES NOT DO AT ONCE, said plainly: an access token already issued
    keeps reading the DATA routes until it lapses, at most
    `access_token_minutes` (ADR 0003). Account and admin routes stop at once,
    and the next refresh is refused.
    """
    _throttle(actor)
    target = _target(db, user_id)
    _may_act_on(actor, target)
    if target.status != "active" and target.status != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, f"account is {target.status}")
    previous = target.status
    target.status = "suspended"
    count = service.revoke_all(db, target)
    _audit(db, request, "suspended", actor, target, previous=previous,
           sessions_revoked=count, reason=(payload.reason or "").strip() or None)
    db.flush()
    return _detail(db, actor, target)


@router.post("/users/{user_id}/reactivate")
def reactivate(user_id: uuid.UUID, request: Request, actor: Staff, db: Db) -> UserDetail:
    """
    Undo a suspension. Back to `active` only if the address was verified:
    reactivating must not become a way around verification.
    """
    _throttle(actor)
    target = _target(db, user_id)
    _may_act_on(actor, target)
    if target.status != "suspended":
        raise HTTPException(status.HTTP_409_CONFLICT, "account is not suspended")
    target.status = "active" if target.email_verified_at else "pending"
    target.failed_attempts = 0
    target.locked_until = None
    _audit(db, request, "reactivated", actor, target, status=target.status)
    db.flush()
    return _detail(db, actor, target)


@router.post("/users/{user_id}/unlock")
def unlock(user_id: uuid.UUID, request: Request, actor: Staff, db: Db) -> UserDetail:
    """Lift the lock-out after too many wrong passwords, before it expires."""
    _throttle(actor)
    target = _target(db, user_id)
    _may_act_on(actor, target)
    if not (target.locked_until and target.locked_until > now()):
        raise HTTPException(status.HTTP_409_CONFLICT, "account is not locked")
    target.failed_attempts = 0
    target.locked_until = None
    _audit(db, request, "unlocked", actor, target)
    db.flush()
    return _detail(db, actor, target)


def _role_change(user_id: uuid.UUID, role: str, grant: bool, request: Request,
                 actor: User, db: Session) -> UserDetail:
    _throttle(actor)
    if role not in GRANTABLE:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"only {sorted(GRANTABLE)} can be granted from here")
    target = _target(db, user_id)
    _may_act_on(actor, target)
    if grant:
        if target.status != "active":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "only an active, verified account can become an administrator")
        service.grant_role(db, target, role, by=actor.id)
    else:
        service.revoke_role(db, target, role, by=actor.id)
    db.flush()
    return _detail(db, actor, target)


@router.post("/users/{user_id}/roles/{role}/grant")
def grant_role(user_id: uuid.UUID, role: str, request: Request,
               actor: SuperAdmin, db: Db) -> UserDetail:
    return _role_change(user_id, role, True, request, actor, db)


@router.post("/users/{user_id}/roles/{role}/revoke")
def revoke_role(user_id: uuid.UUID, role: str, request: Request,
                actor: SuperAdmin, db: Db) -> UserDetail:
    return _role_change(user_id, role, False, request, actor, db)

