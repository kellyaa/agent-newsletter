"""Record each run.sh invocation in state.db's run_attempts table.

run.sh calls this twice per run:

  run_attempt.py start                     → inserts a row, prints its attempt number
  run_attempt.py finish ATTEMPT ok|failed [--stage S] [--exit-code N]
  run_attempt.py latest                    → outcome of today's latest attempt for
                                             this edition: ok | failed | started | none
                                             (the orchestrator uses it to skip finished
                                             editions and to pick the ones to re-run)

The `runs` table only gets a row when publish succeeds, and `--force` deletes
it, so failed attempts and re-runs leave no trace there. This table keeps one
row per attempt, so run history (ship rate, failed stage, which code ran,
spend per attempt) can be read straight from the state.db on the content
branch. A row left at outcome='started' means the run died without reaching
finish (killed pod, lost shell).

Nothing in the pipeline reads this table, so it can't change what ships.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone

from db import (
    REPO_ROOT, connect, db_path, finish_run_attempt, init_db, insert_run_attempt,
    latest_attempt_outcome, next_attempt_number, run_date,
)
from llm import usage_totals
from models import AttemptOutcome, NO_ATTEMPT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("run_attempt")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def code_state() -> tuple[str | None, int | None]:
    """(HEAD commit, 1 if tracked files differ from it) of the code checkout."""
    def git(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", "-C", str(REPO_ROOT), *args],
                capture_output=True, text=True, check=True, timeout=30,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=no")
    return commit or None, None if status is None else int(bool(status))


def start(conn, date: str, started_at: str) -> int:
    attempt = next_attempt_number(conn, date)
    commit, dirty = code_state()
    insert_run_attempt(conn, date, attempt, started_at, commit, dirty, socket.gethostname())
    return attempt


def finish(conn, date: str, attempt: int, outcome: str, stage: str | None,
           exit_code: int | None, usage: dict | None) -> None:
    updated = finish_run_attempt(
        conn, date, attempt, finished_at=_now(), outcome=outcome,
        failed_stage=stage if outcome == AttemptOutcome.FAILED else None,
        exit_code=exit_code, usage=usage or {},
    )
    if updated == 0:
        log.warning("no run_attempts row for %s attempt %d; nothing updated", date, attempt)


def latest_outcome(conn, date: str) -> str:
    outcome = latest_attempt_outcome(conn, date)
    return NO_ATTEMPT if outcome is None else outcome


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    sub.add_parser("latest")
    f = sub.add_parser("finish")
    f.add_argument("attempt", type=int)
    f.add_argument("outcome", choices=[AttemptOutcome.OK, AttemptOutcome.FAILED])
    f.add_argument("--stage")
    f.add_argument("--exit-code", type=int)
    args = ap.parse_args(argv)

    date = run_date()
    if args.cmd == "latest":
        # Read-only: never create an edition's DB just to ask about it.
        if not db_path().exists():
            print(NO_ATTEMPT)
            return 0
        conn = connect()
        try:
            print(latest_outcome(conn, date))
        finally:
            conn.close()
        return 0
    init_db()
    conn = connect()
    try:
        if args.cmd == "start":
            print(start(conn, date, os.environ.get("RUN_STARTED_AT") or _now()))
        else:
            usage = usage_totals(os.environ.get("LLM_USAGE_LOG"), attempt=str(args.attempt))
            finish(conn, date, args.attempt, args.outcome, args.stage, args.exit_code, usage)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
