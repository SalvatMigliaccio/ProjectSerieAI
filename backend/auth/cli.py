"""
Administration from the machine, not from the web.

WHY A COMMAND AND NOT AN ENDPOINT. Somebody has to hold `superadmin` before
anyone can grant it, and there is no honest way to bootstrap that over HTTP:
an endpoint that mints the first administrator is an endpoint that mints the
second one too, for whoever finds it first. Running locally is itself the
credential — you already have the database password.

For the same reason there is no "promote to superadmin" route anywhere in the
API. Public signup grants `customer` and nothing else, so no sequence of HTTP
calls can escalate.

    python -m backend.auth.cli create-superadmin --email you@example.com
    python -m backend.auth.cli list-users
    python -m backend.auth.cli grant --email x@example.com --role admin
    python -m backend.auth.cli check-config
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from . import service
from .db import session_scope
from .models import Role, User, now
from .password import WeakPassword
from .password import hash_password as hash_pw
from .settings import settings


def _ask_password() -> str:
    """
    Read it twice, from the terminal, never from an argument.

    A password passed as `--password` lands in the shell history and in the
    process list, where any other user on the machine can read it with `ps`.
    """
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Repeat: "):
        raise SystemExit("the two passwords do not match")
    return first


def create_superadmin(args: argparse.Namespace) -> int:
    with session_scope() as db:
        existing = service.by_email(db, args.email)
        if existing is not None:
            # Promoting is a legitimate, separate action; doing it silently
            # here would make a typo in the address a privilege grant.
            print(f"{args.email} already exists. To promote it:\n"
                  f"  python -m backend.auth.cli grant --email {args.email} "
                  f"--role superadmin", file=sys.stderr)
            return 1

        try:
            password = _ask_password()
            user = User(
                email=args.email.strip(),
                password_hash=hash_pw(password),
                # Active immediately, with the address marked verified: the
                # point of verification is proving control of the mailbox, and
                # whoever runs this already has the database.
                status="active",
                email_verified_at=now(),
            )
            db.add(user)
            db.flush()
            service.grant_role(db, user, "superadmin")
        except WeakPassword as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1

        print(f"created {user.email} with role superadmin")
        return 0


def list_users(args: argparse.Namespace) -> int:
    with session_scope() as db:
        users = db.scalars(select(User).order_by(User.created_at)).all()
        if not users:
            print("no users")
            return 0
        width = max(len(u.email) for u in users)
        for u in users:
            roles = ", ".join(sorted(r.name for r in u.roles)) or "-"
            locked = " LOCKED" if u.locked_until and u.locked_until > now() else ""
            print(f"{u.email:<{width}}  {u.status:<9} {roles}{locked}")
        return 0


def grant(args: argparse.Namespace) -> int:
    with session_scope() as db:
        user = service.by_email(db, args.email)
        if user is None:
            print(f"no such user: {args.email}", file=sys.stderr)
            return 1
        try:
            service.grant_role(db, user, args.role)
        except LookupError as exc:
            known = ", ".join(sorted(r.name for r in db.scalars(select(Role)).all()))
            print(f"{exc}\nknown roles: {known}", file=sys.stderr)
            return 1
        print(f"{args.email} now holds {args.role}")
        return 0


def revoke(args: argparse.Namespace) -> int:
    with session_scope() as db:
        user = service.by_email(db, args.email)
        if user is None:
            print(f"no such user: {args.email}", file=sys.stderr)
            return 1
        service.revoke_role(db, user, args.role)
        print(f"{args.email} no longer holds {args.role}")
        return 0


def suspend(args: argparse.Namespace) -> int:
    """Suspending drops the sessions too, or the user stays in until they expire."""
    with session_scope() as db:
        user = service.by_email(db, args.email)
        if user is None:
            print(f"no such user: {args.email}", file=sys.stderr)
            return 1
        user.status = "suspended"
        count = service.revoke_all(db, user)
        service.record_event(db, "suspended", "ok", user_id=user.id,
                             details={"sessions_revoked": count})
        print(f"{args.email} suspended, {count} session(s) revoked")
        return 0


def check_config(args: argparse.Namespace) -> int:
    """
    What would be wrong if this configuration were production.

    Exits non-zero when there is something to fix, so it can be a deployment
    gate rather than a thing someone reads.
    """
    problems = settings().production_problems()
    if not problems:
        print("configuration looks fit for production")
        return 0
    print("NOT fit for production:", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m backend.auth.cli",
        description="Local administration for accounts and roles.")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-superadmin", help="create the first administrator")
    p.add_argument("--email", required=True)
    p.set_defaults(func=create_superadmin)

    p = sub.add_parser("list-users", help="every account, with roles and status")
    p.set_defaults(func=list_users)

    p = sub.add_parser("grant", help="give a role")
    p.add_argument("--email", required=True)
    p.add_argument("--role", required=True)
    p.set_defaults(func=grant)

    p = sub.add_parser("revoke", help="take a role away")
    p.add_argument("--email", required=True)
    p.add_argument("--role", required=True)
    p.set_defaults(func=revoke)

    p = sub.add_parser("suspend", help="block an account and drop its sessions")
    p.add_argument("--email", required=True)
    p.set_defaults(func=suspend)

    p = sub.add_parser("check-config", help="refuse an unsafe production setup")
    p.set_defaults(func=check_config)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
