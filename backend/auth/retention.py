"""
Deleting what the privacy policy says is no longer kept.

WHY A COMMAND AND NOT A TIMER INSIDE THE API. The API runs as short-lived
serverless instances: nothing in it lives long enough to be a scheduler, and a
purge started by a request would make the request's latency depend on how
much history piled up. The command runs from the same scheduler as the
pipeline (`python -m backend.auth.cli purge`) and is idempotent: running it
twice, or a day late, changes nothing that matters.

THE PERIODS ARE A PROMISE, NOT A TUNING KNOB. They are published on the
privacy page, and `tests/test_privacy.py` ties the two together. See
`Settings.audit_retention_days` and the three after it.

WHAT IS NEVER TOUCHED. Live sessions, active and suspended accounts, and the
track record — which is not in this database at all.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import AuthEvent, EmailToken, User, UserSession, now
from .settings import Settings, settings


@dataclass(frozen=True)
class Rule:
    """One table, and which of its rows are past their period."""

    name: str
    model: type
    condition: object


def rules(cfg: Settings, at: dt.datetime) -> list[Rule]:
    """The four periods of the privacy policy, as WHERE clauses."""
    days = dt.timedelta(days=1)
    return [
        Rule("auth_events", AuthEvent,
             AuthEvent.at < at - cfg.audit_retention_days * days),
        # A session is past its period once it has been unusable for that
        # long. `expires_at` is the absolute ceiling, so a session that was
        # never revoked still counts from the day it could last be used.
        Rule("sessions", UserSession,
             func.coalesce(UserSession.revoked_at, UserSession.expires_at)
             < at - cfg.session_retention_days * days),
        Rule("email_tokens", EmailToken,
             EmailToken.expires_at < at - cfg.token_retention_days * days),
        Rule("pending_users", User,
             (User.status == "pending")
             & (User.created_at < at - cfg.pending_account_days * days)),
    ]


def purge(db: Session, *, cfg: Settings | None = None, at: dt.datetime | None = None,
          dry_run: bool = False) -> dict[str, int]:
    """
    Delete every row past its period; return how many per rule.

    `dry_run` counts with the same WHERE clauses it would delete with, so what
    it prints is exactly what the real run removes — two code paths would be
    two answers.

    Pending users go LAST: deleting them cascades onto their tokens and
    sessions, and counting those first keeps each row in the rule that names
    why it went.
    """
    cfg = cfg or settings()
    at = at or now()
    counts: dict[str, int] = {}
    for rule in rules(cfg, at):
        counts[rule.name] = db.scalar(
            select(func.count()).select_from(rule.model).where(rule.condition)) or 0
        if not dry_run and counts[rule.name]:
            db.execute(delete(rule.model).where(rule.condition))
    return counts

