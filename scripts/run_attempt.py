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

from db import DB_PATH, REPO_ROOT, connect, init_db, run_date
from publish import usage_totals

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
    attempt = conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) + 1 FROM run_attempts WHERE date = ?", (date,)
    ).fetchone()[0]
    commit, dirty = code_state()
    conn.execute(
        "INSERT INTO run_attempts (date, attempt, started_at, outcome, code_commit, code_dirty, host)"
        " VALUES (?, ?, ?, 'started', ?, ?, ?)",
        (date, attempt, started_at, commit, dirty, socket.gethostname()),
    )
    conn.commit()
    return attempt


def finish(conn, date: str, attempt: int, outcome: str, stage: str | None,
           exit_code: int | None, usage: dict | None) -> None:
    usage = usage or {}
    cur = conn.execute(
        "UPDATE run_attempts SET finished_at = ?, outcome = ?, failed_stage = ?, exit_code = ?,"
        " llm_calls = ?, tokens_in = ?, tokens_out = ?, models = ?, tokens_by_model = ?"
        " WHERE date = ? AND attempt = ?",
        (_now(), outcome, stage if outcome == "failed" else None, exit_code,
         usage.get("calls"), usage.get("tokens_in"), usage.get("tokens_out"),
         usage.get("models"), usage.get("tokens_by_model"), date, attempt),
    )
    conn.commit()
    if cur.rowcount == 0:
        log.warning("no run_attempts row for %s attempt %d; nothing updated", date, attempt)


def latest_outcome(conn, date: str) -> str:
    row = conn.execute(
        "SELECT outcome FROM run_attempts WHERE date = ? ORDER BY attempt DESC LIMIT 1", (date,)
    ).fetchone()
    return row[0] if row else "none"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("start")
    sub.add_parser("latest")
    f = sub.add_parser("finish")
    f.add_argument("attempt", type=int)
    f.add_argument("outcome", choices=["ok", "failed"])
    f.add_argument("--stage")
    f.add_argument("--exit-code", type=int)
    args = ap.parse_args(argv)

    date = run_date()
    if args.cmd == "latest":
        # Read-only: never create an edition's DB just to ask about it.
        if not DB_PATH.exists():
            print("none")
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
