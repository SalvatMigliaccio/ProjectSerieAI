"""
Records what the scheduler did, in a shape a machine can read.

WHY A JSON FILE AND NOT THE LOG. `/api/status` has to say whether Friday's
`predict_round` ran and how it went. The obvious way is to parse the tail of
`logs/scheduler_YYYYMMDD.log` and look for the FATTO / IN SOSPESO lines — and
it is the wrong way: the day someone rewords a summary line, the API keeps
answering, with stale or invented content. A parser that fails silently is
worse than no parser.

So the scheduler writes the facts here — which command, when, exit code, which
log file — and the log stays what it is, something a person reads. Everything
else `/api/status` needs (current round, its state, snapshot age) is derived
from the data itself, which cannot drift out of sync with reality.

WHAT IT DELIBERATELY DOES NOT STORE. No matchday, no counts of written or
pending predictions: those would have to come from parsing stdout. The API
reads them from the track record instead, where they are facts rather than
prose.

THIS FILE IS NOT THE TRACK RECORD. It lives in `logs/`, it is disposable, and
losing it costs a "never run" on a dashboard — not a prediction.

Usage (from scripts/run_task.cmd, never by hand):
    python -m backend.api.last_run --command predict_round --exit-code 0 \
        --log scheduler_20260918.log --started-at 2026-09-18T17:15:02Z
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src import config

LOG_DIR = config.ROOT / "logs"
LAST_RUN = LOG_DIR / "last_run.json"

COMMANDS = ("predict_round", "close_round")


def read(path: Path = LAST_RUN) -> dict:
    """Every recorded run, keyed by command. Empty dict if nothing ran yet."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def record(command: str, exit_code: int, log_name: str | None = None,
           started_at: str | None = None, path: Path = LAST_RUN) -> dict:
    """
    Add one run, keeping the other command's entry untouched.

    The file holds the LAST run of each command, not a history: the history is
    the dated log files, and duplicating it here would mean two truths.
    """
    now = pd.Timestamp.now(tz="UTC").isoformat().replace("+00:00", "Z")
    entry = {
        "command": command,
        "started_at": started_at or now,
        "finished_at": now,
        "exit_code": int(exit_code),
        "ok": int(exit_code) == 0,
        "log": log_name,
    }
    data = read(path)
    data[command] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return entry


def main() -> None:
    ap = argparse.ArgumentParser(description="Record a scheduler run for /api/status")
    ap.add_argument("--command", required=True, choices=COMMANDS)
    ap.add_argument("--exit-code", type=int, required=True)
    ap.add_argument("--log", help="name of the dated log file")
    ap.add_argument("--started-at", help="ISO 8601 UTC, captured before the run")
    args = ap.parse_args()

    entry = record(args.command, args.exit_code, args.log, args.started_at)
    print(f"last_run: {entry['command']} exit={entry['exit_code']} "
          f"at {entry['finished_at']}")


if __name__ == "__main__":
    main()
