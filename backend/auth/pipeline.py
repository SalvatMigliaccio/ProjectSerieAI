"""
Launching `predict_round` and `close_round` from the admin panel (ADR 0007, phase 3).

THE SHAPE: A QUEUE, NOT A CALL. The button inserts a `queued` row in
`pipeline_jobs`; a worker on the pipeline machine (`python -m backend.worker`,
on the owner's PC today, on a VPS later) claims it, runs the same command the
Windows scheduler runs, and writes back exit code and output. The API never
runs a model: it cannot (Vercel has minutes, no disk, no browser for
WhoScored), and it must not (`test_no_model_is_loaded`).

WHY THIS DOES NOT REOPEN THE OLD RISK. The rule was "no route launches
predict_round", because a write route over the registry is how a prediction
gets written after the final whistle. Here the web can only ASK for an
idempotent command that the scheduler would run anyway, from a closed list,
with flags from a closed list. What the predictions are is decided by the
command, and whether they are accepted by the registry's own rules in
Postgres (append-only, kickoff in the future, server clock). A request
queued at the wrong moment produces "nothing to do", not a late prediction.

WHO: `system:manage` (superadmin) to queue and cancel; `users:manage` to look.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import service
from .deps import CurrentUser, Db, client_ip
from .models import PipelineJob, PipelineWorker, User, now
from .ratelimit import admin_action_limiter

COMMANDS = ("predict_round", "close_round")
# Flag name -> the command-line switch the worker passes. Nothing else exists.
OPTIONS = {"dry_run": "--dry-run", "skip_ingest": "--skip-ingest"}

# A running job whose worker has not written for this long is presumed dead.
STALE_AFTER = dt.timedelta(minutes=10)
# A worker is "online" if it polled within this many of its own intervals.
ONLINE_POLLS = 3
ONLINE_FLOOR = dt.timedelta(minutes=2)

TAIL_CHARS = 20_000


class AlreadyActive(Exception):
    """Another job is queued or running: the database allows one."""


# ---------------------------------------------------------------------------
# Service: shared by the routes and the worker
# ---------------------------------------------------------------------------

def clean_options(raw: dict | None) -> dict:
    """Only known flags, only booleans. Anything else is a 422, not ignored."""
    raw = raw or {}
    unknown = set(raw) - set(OPTIONS)
    if unknown:
        raise ValueError(f"unknown options: {sorted(unknown)}")
    if not all(isinstance(v, bool) for v in raw.values()):
        raise ValueError("options are booleans")
    return {k: v for k, v in raw.items() if v}


def argv_for(job: PipelineJob) -> list[str]:
    """
    The module and switches for a job. The ONLY way a job becomes a command.

    The command name is checked against the list again here, after the
    database CHECK, because this is the line that ends in a subprocess.
    """
    if job.command not in COMMANDS:
        raise ValueError(f"not a pipeline command: {job.command!r}")
    flags = [OPTIONS[k] for k in sorted(clean_options(job.options))]
    extra = ["--no-open"] if job.command == "predict_round" else []
    return ["-m", f"goalmodel.prediction.{job.command}", *extra, *flags]


def enqueue(db: Session, actor: User | None, command: str, options: dict) -> PipelineJob:
    """A queued job. `actor` None is the scheduler (`worker --accoda`)."""
    if command not in COMMANDS:
        raise ValueError(f"not a pipeline command: {command!r}")
    job = PipelineJob(command=command, options=clean_options(options),
                      status="queued", requested_by=actor.id if actor else None)
    try:
        # A savepoint, so a refusal by the one-active index does not abort the
        # whole transaction. The `add` goes INSIDE it: added before, the
        # refused row would stay pending in the session and be flushed again
        # by whatever the caller does next.
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError as exc:
        raise AlreadyActive("another job is queued or running") from exc
    return job


def claim(db: Session, worker: str) -> PipelineJob | None:
    """
    The oldest queued job, now `running` for `worker`. None if there is none.

    `FOR UPDATE SKIP LOCKED`: two workers polling at once cannot both take it.
    """
    row = db.execute(text(
        "UPDATE pipeline_jobs SET status = 'running', started_at = now(), "
        "heartbeat_at = now(), worker = :w "
        "WHERE id = (SELECT id FROM pipeline_jobs WHERE status = 'queued' "
        "ORDER BY requested_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id"),
        {"w": worker}).first()
    return db.get(PipelineJob, row[0], populate_existing=True) if row else None


def progress(db: Session, job_id: uuid.UUID, tail: str) -> None:
    db.execute(text("UPDATE pipeline_jobs SET heartbeat_at = now(), log_tail = :t "
                    "WHERE id = :i AND status = 'running'"),
               {"t": tail[-TAIL_CHARS:], "i": job_id})


def finish(db: Session, job_id: uuid.UUID, exit_code: int, tail: str) -> None:
    db.execute(text(
        "UPDATE pipeline_jobs SET status = :s, finished_at = now(), heartbeat_at = now(), "
        "exit_code = :c, log_tail = :t WHERE id = :i AND status = 'running'"),
        {"s": "done" if exit_code == 0 else "failed", "c": exit_code,
         "t": tail[-TAIL_CHARS:], "i": job_id})


def recover(db: Session, worker: str) -> int:
    """
    At start-up: jobs this worker left `running` died with it. Mark them failed.

    Only this worker's own: another worker's job may well be alive.
    """
    res = db.execute(text(
        "UPDATE pipeline_jobs SET status = 'failed', finished_at = now(), exit_code = -1, "
        "log_tail = coalesce(log_tail, '') || :note "
        "WHERE status = 'running' AND worker = :w"),
        {"w": worker, "note": "\n[worker restarted: this run was interrupted]\n"})
    return res.rowcount or 0


def beat(db: Session, name: str, host: str, started: dt.datetime, poll_seconds: int) -> None:
    db.execute(text(
        "INSERT INTO pipeline_workers (name, host, started_at, last_seen_at, poll_seconds) "
        "VALUES (:n, :h, :s, now(), :p) ON CONFLICT (name) DO UPDATE SET host = :h, "
        "started_at = :s, last_seen_at = now(), poll_seconds = :p"),
        {"n": name, "h": host, "s": started, "p": poll_seconds})


def online(worker: PipelineWorker, at: dt.datetime) -> bool:
    """Polled within three of its own intervals (two minutes at least)."""
    window = max(dt.timedelta(seconds=worker.poll_seconds * ONLINE_POLLS), ONLINE_FLOOR)
    return at - worker.last_seen_at <= window


def any_online(db: Session, at: dt.datetime) -> bool:
    return any(online(w, at) for w in db.scalars(select(PipelineWorker)))


def is_stale(job: PipelineJob, at: dt.datetime) -> bool:
    return (job.status == "running" and job.heartbeat_at is not None
            and at - job.heartbeat_at > STALE_AFTER)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/admin/pipeline", tags=["admin"])


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


class JobRow(BaseModel):
    id: str
    command: str
    options: dict
    status: str
    requested_by: str | None = None
    requested_at: str
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None
    worker: str | None = None
    exit_code: int | None = None
    stale: bool
    log_tail: str | None = None


class WorkerRow(BaseModel):
    name: str
    host: str | None = None
    started_at: str
    last_seen_at: str
    poll_seconds: int
    online: bool


class PipelineState(BaseModel):
    workers: list[WorkerRow]
    any_online: bool
    active: JobRow | None = None
    jobs: list[JobRow]
    can_launch: bool


class LaunchPayload(BaseModel):
    command: str
    options: dict = {}


def _iso(v: dt.datetime | None) -> str | None:
    return v.isoformat() if v else None


def _job(db: Session, j: PipelineJob, at: dt.datetime, *, with_log: bool) -> JobRow:
    who = db.get(User, j.requested_by) if j.requested_by else None
    return JobRow(
        id=str(j.id), command=j.command, options=j.options or {}, status=j.status,
        requested_by=who.email if who else None, requested_at=j.requested_at.isoformat(),
        started_at=_iso(j.started_at), finished_at=_iso(j.finished_at),
        heartbeat_at=_iso(j.heartbeat_at), worker=j.worker, exit_code=j.exit_code,
        stale=is_stale(j, at), log_tail=j.log_tail if with_log else None)


def _state(db: Session, actor: User) -> PipelineState:
    at = now()
    workers = []
    for w in db.scalars(select(PipelineWorker).order_by(PipelineWorker.last_seen_at.desc())):
        workers.append(WorkerRow(
            name=w.name, host=w.host, started_at=w.started_at.isoformat(),
            last_seen_at=w.last_seen_at.isoformat(), poll_seconds=w.poll_seconds,
            online=online(w, at)))
    jobs = db.scalars(select(PipelineJob).order_by(PipelineJob.requested_at.desc())
                      .limit(20)).all()
    active = next((j for j in jobs if j.status in ("queued", "running")), None)
    return PipelineState(
        workers=workers, any_online=any(w.online for w in workers),
        active=_job(db, active, at, with_log=True) if active else None,
        jobs=[_job(db, j, at, with_log=False) for j in jobs],
        can_launch=service.has_permission(actor, "system:manage"))


def _throttle(actor: User) -> None:
    key = str(actor.id)
    if not admin_action_limiter.allow(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many actions",
                            headers={"Retry-After": str(admin_action_limiter.retry_after(key))})


@router.get("")
def state(actor: Staff, db: Db) -> PipelineState:
    return _state(db, actor)


@router.get("/jobs/{job_id}")
def job_detail(job_id: uuid.UUID, actor: Staff, db: Db) -> JobRow:
    job = db.get(PipelineJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
    return _job(db, job, now(), with_log=True)


@router.post("/jobs", status_code=status.HTTP_201_CREATED)
def launch(payload: LaunchPayload, request: Request, actor: SuperAdmin, db: Db) -> PipelineState:
    _throttle(actor)
    try:
        job = enqueue(db, actor, payload.command, payload.options)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except AlreadyActive as exc:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "a job is already queued or running: wait for it to finish") from exc
    service.record_event(db, "pipeline_requested", "ok", user_id=actor.id,
                         ip=client_ip(request), user_agent=request.headers.get("user-agent"),
                         details={"by": str(actor.id), "job": str(job.id),
                                  "command": job.command, "options": job.options})
    db.flush()
    return _state(db, actor)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: uuid.UUID, request: Request, actor: SuperAdmin, db: Db) -> PipelineState:
    """
    Withdraw a queued job, or give up on a running one whose worker died.

    A LIVE running job is not cancellable from here: the command may be
    halfway through writing, and killing it from a web page is exactly the
    kind of remote control this design avoids. Stop the worker on its machine.
    """
    _throttle(actor)
    job = db.get(PipelineJob, job_id, with_for_update=True)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such job")
    at = now()
    if job.status == "queued":
        job.status = "cancelled"
    elif is_stale(job, at):
        job.status = "failed"
        job.exit_code = -1
        job.log_tail = (job.log_tail or "") + "\n[marked failed from the panel: worker silent]\n"
    else:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "only a queued job, or a running one whose worker went silent, "
                            "can be cancelled")
    job.finished_at = at
    service.record_event(db, "pipeline_cancelled", "ok", user_id=actor.id,
                         ip=client_ip(request), user_agent=request.headers.get("user-agent"),
                         details={"by": str(actor.id), "job": str(job.id), "command": job.command})
    db.flush()
    return _state(db, actor)
