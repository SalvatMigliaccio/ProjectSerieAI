"""
The pipeline worker: takes the jobs the admin panel queues and runs them here.

    python -m backend.worker              # poll every 30 s, forever
    python -m backend.worker --once       # take at most one job and exit
    python -m backend.worker --poll 60 --name casa
    python -m backend.worker --accoda predict_round   # queue it, as the scheduler

`--accoda` IS WHAT THE SCHEDULED TASKS SHOULD CALL. If the Friday task runs
`predict_round` directly while a `close_round` from the panel is running, the
two write the same files at once. Queued, they run one at a time, and the
panel shows the scheduled runs next to the manual ones.

WHERE IT RUNS (ADR 0007, phase 3). On the machine that has the pipeline: the
owner's PC today, a VPS later — the same code in both places, which is why it
is Python and not a `.cmd`. It needs the `ingest` and `ml` extras (it runs the
model), the `auth` extra (it talks to Postgres), and `AI_NAPLES_DATABASE_URL`
pointing at the same database as the site.

WHAT IT RUNS. Exactly what `backend/scripts/run_task.cmd` runs for the Windows
scheduler: `python -m goalmodel.prediction.<command>`, from the repository
root, output appended to a dated log in `logs/`, then `logs/last_run.json`
updated for `/api/status`. The argv comes from `pipeline.argv_for`, which maps
a name from a closed list to fixed switches: nothing read from the database
is ever executed as a command line.

OUTBOUND ONLY. The worker opens a connection to Postgres and nothing else; it
listens on no port. A PC behind a home router needs no forwarding, and a VPS
exposes nothing new.

WHILE A JOB RUNS the last part of its output is written to the job every few
seconds, so the panel shows it live, and the job's heartbeat moves: a job
whose heartbeat stops is a worker that died, and the panel lets the owner mark
it failed.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from goalmodel import config

from backend.api import last_run
from backend.auth import pipeline
from backend.auth.db import session_scope

log = logging.getLogger("worker")

ROOT = config.ROOT
LOG_DIR = ROOT / "logs"
TAIL_LINES = 400
PUSH_EVERY = 5.0          # seconds between two writes of the output tail
# One hour, as the scheduled tasks had (`-ExecutionTimeLimit 1h`). A hung
# predict_round must die before the next Friday slot: while it is the active
# job, the 19:15 and 20:00 runs would only find "already active" and skip.
TIMEOUT = dt.timedelta(hours=1)


# The site's database (Neon) on the owner's PC, where `.env` points at the
# local Docker Postgres used for development. Git ignores it.
PRODUCTION_ENV = ROOT / ".env.produzione"
PRODUCTION_KEYS = ("AI_NAPLES_DATABASE_URL", "GOALMODEL_")


def load_production_env(path: Path = PRODUCTION_ENV) -> bool:
    """
    Put the production database URL (and `GOALMODEL_*`) in the environment.

    WHY. The worker must queue and claim jobs in THE SITE'S database: a worker
    reading the local `.env` would poll a Docker Postgres that the panel on
    Vercel never writes to, and every button would wait forever. Environment
    variables beat `.env` in `backend.auth.settings` and in `goalmodel.pg`,
    and the child processes inherit them, so `predict_round` writes the
    registry in the same database the site reads.

    Called before the first database access, because `settings()` is cached.
    """
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            key, sep, value = line.strip().partition("=")
            key = key.strip()
            if sep and key.startswith(PRODUCTION_KEYS):
                os.environ[key] = value.strip().strip('"').strip("'")
    return True


def _child_env() -> dict[str, str]:
    """
    The worker's environment, plus the `GOALMODEL_*` keys of `.env` it lacks.

    The scheduler sets `GOALMODEL_STORAGE` in the task; a worker started by
    hand would silently fall back to `parquet` and write the CSV instead of
    the database the site reads. Reading the same `.env` the rest of the
    project reads closes that gap. Values already in the environment win.
    """
    env = dict(os.environ)
    dotenv = ROOT / ".env"
    if dotenv.exists():
        with dotenv.open(encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.strip().partition("=")
                key = key.strip()
                if sep and key.startswith("GOALMODEL_") and key not in env:
                    env[key] = value.strip().strip('"').strip("'")
    env["PYTHONUNBUFFERED"] = "1"     # live output, not a block at the end
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(job_id, argv: list[str], command: str, *, record: bool = True) -> int:
    """Run one job to the end, streaming its output. Returns the exit code."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started = dt.datetime.now(dt.UTC)
    log_name = f"worker_{started:%Y%m%d}.log"
    tail: collections.deque[str] = collections.deque(maxlen=TAIL_LINES)
    lock = threading.Lock()

    def push(final: bool = False, code: int | None = None) -> None:
        with lock:
            text = "".join(tail)
        with session_scope() as db:
            if final:
                pipeline.finish(db, job_id, code if code is not None else -1, text)
            else:
                pipeline.progress(db, job_id, text)

    cmd = [sys.executable, *argv]
    with (LOG_DIR / log_name).open("a", encoding="utf-8") as fh:
        header = f"\n{'=' * 72}\n[{started:%Y-%m-%dT%H:%M:%SZ}] START {command} (job {job_id}) {' '.join(argv)}\n{'=' * 72}\n"
        fh.write(header)
        tail.append(header.lstrip("\n"))
        try:
            proc = subprocess.Popen(cmd, cwd=ROOT, env=_child_env(), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                    errors="replace", bufsize=1)
        except OSError as exc:
            msg = f"[worker] could not start: {exc}\n"
            fh.write(msg)
            tail.append(msg)
            push(final=True, code=127)
            return 127

        stop = threading.Event()

        def pusher() -> None:
            while not stop.wait(PUSH_EVERY):
                try:
                    push()
                except Exception as exc:  # noqa: BLE001 - a missed update is not a failed job
                    log.warning("progress not written: %s", exc)

        t = threading.Thread(target=pusher, daemon=True)
        t.start()
        deadline = time.monotonic() + TIMEOUT.total_seconds()
        for line in proc.stdout or ():
            fh.write(line)
            fh.flush()
            with lock:
                tail.append(line)
            if time.monotonic() > deadline:
                proc.kill()
                with lock:
                    tail.append(f"\n[worker] killed after {TIMEOUT}\n")
                break
        code = proc.wait()
        stop.set()
        t.join(timeout=PUSH_EVERY * 2)
        end = f"[END] {command} exit={code}\n"
        fh.write(end)
        tail.append(end)

    push(final=True, code=code)
    # The same record the scheduler writes, so /api/status tells the truth
    # whoever launched the run. NOT for a dry run: it touched nothing, and
    # /api/status would report it as the last real predict or close.
    if record:
        last_run.record(command, code, log_name,
                        started.strftime("%Y-%m-%dT%H:%M:%SZ"))
    return code


def poll_once(name: str, host: str, started: dt.datetime, poll: int) -> bool:
    """Heartbeat, then at most one job. True if a job ran."""
    with session_scope() as db:
        pipeline.beat(db, name, host, started, poll)
        job = pipeline.claim(db, name)
        if job is None:
            return False
        job_id, command = job.id, job.command
        dry = bool((job.options or {}).get("dry_run"))
        try:
            argv = pipeline.argv_for(job)
        except ValueError as exc:
            pipeline.finish(db, job_id, 2, f"[worker] refused: {exc}\n")
            return True
    log.info("job %s: %s %s", job_id, command, " ".join(argv))
    code = run(job_id, argv, command, record=not dry)
    log.info("job %s finished, exit %s", job_id, code)
    return True


# Exit code of `--accoda` when the job was NOT handed to a worker. The
# scheduler script then runs the command itself, as it always did.
NOT_HANDED_OFF = 10


def accoda(command: str) -> int:
    """
    Queue `command` for the worker, as the scheduler. 0 if handed off.

    NOT_HANDED_OFF (10) when nobody would run it: the database does not answer
    (Docker off), or no worker polled recently (worker stopped). Before the
    queue existed `predict_round` needed no database at all; a scheduled
    Friday run must not start depending on one. `accoda.cmd` reads the 10 and
    runs the command directly, which is exactly the old behaviour.

    0 also when another job is already active: the queue is doing its job, and
    the command is idempotent anyway. Said in the log.
    """
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with session_scope() as db:
            if not pipeline.any_online(db, dt.datetime.now(dt.UTC)):
                log.warning("%s not queued: no worker online. Run it directly.", command)
                return NOT_HANDED_OFF
            try:
                job = pipeline.enqueue(db, None, command, {})
            except pipeline.AlreadyActive:
                log.info("%s not queued: another job is already queued or running", command)
                return 0
            log.info("queued %s as job %s", command, job.id)
            return 0
    except (SQLAlchemyError, OSError) as exc:
        log.warning("%s not queued: database unreachable (%s). Run it directly.",
                    command, type(exc).__name__)
        return NOT_HANDED_OFF


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.worker",
                                 description="Run the pipeline jobs queued from the admin panel.")
    ap.add_argument("--once", action="store_true", help="take at most one job, then exit")
    ap.add_argument("--poll", type=int, default=30, help="seconds between polls (default 30)")
    ap.add_argument("--name", default=None, help="worker name (default: the host name)")
    ap.add_argument("--accoda", choices=pipeline.COMMANDS,
                    help="queue this command (as the scheduler) and exit")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    if load_production_env():
        log.info("database from %s (the site's), not from .env", PRODUCTION_ENV.name)

    if args.accoda:
        return accoda(args.accoda)

    host = socket.gethostname()
    name = args.name or host
    poll = max(5, args.poll)
    started = dt.datetime.now(dt.UTC)
    with session_scope() as db:
        lost = pipeline.recover(db, name)
    if lost:
        log.warning("%d job(s) left running by a previous run of '%s': marked failed", lost, name)
    log.info("worker '%s' on %s, polling every %ds, repo %s", name, host, poll, ROOT)

    while True:
        try:
            ran = poll_once(name, host, started, poll)
        except Exception as exc:  # noqa: BLE001 - the database being down is not fatal
            log.error("poll failed: %s", exc)
            ran = False
        if args.once:
            return 0
        if not ran:
            time.sleep(poll)


if __name__ == "__main__":
    raise SystemExit(main())
