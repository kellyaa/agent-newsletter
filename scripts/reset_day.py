"""Reset one edition's state for a forced re-run (run_edition.sh --force/--refetch).

    EDITION=<slug> python scripts/reset_day.py [--refetch]

* ``--refetch``: delete today's fetched items so fetch runs again (arXiv is
  rate-limited, so this is the expensive option). Runs before the force reset,
  which keys off rows that this deletes.
* always: send today's processed items back to ``candidate`` so rank/write can
  re-run, drop today's ``runs`` row and today's issue file.

Only the selected edition's DB and issue file are touched.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import db
import edition
from db import CONTENT_ROOT, connect, run_date
from models import MetaKey

# Shared with fetch.FETCH_DONE_KEY via models (fetch itself is not imported: it pulls in httpx etc.).
FETCH_DONE_KEY = MetaKey.FETCH_DONE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reset_day")


def refetch(conn, today: str) -> int:
    # Clear fetch's completion marker too: deleting first_seen_date rows alone
    # leaves older items whose last_seen_date was bumped to today, which made
    # fetch skip and the write stage overwrite the live issue (#321).
    db.delete_meta(conn, FETCH_DONE_KEY)
    return db.delete_items_first_seen_on(conn, today)


def force_reset(conn, today: str) -> None:
    # Leave 'new' and 'dropped' alone; see db.reset_processed_items_for for
    # why the match is first_seen_date/published_date (#321, #326).
    db.reset_processed_items_for(conn, today)
    db.delete_run(conn, today)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args(argv)

    paths = edition.runtime_paths(CONTENT_ROOT)
    today = run_date()
    if not paths.state_db.exists():
        log.info("no state.db at %s; nothing to reset", paths.state_db)
        return 0
    conn = connect(paths.state_db)
    try:
        if args.refetch:
            log.info("REFETCH: deleted %d items first seen on %s", refetch(conn, today), today)
        force_reset(conn, today)
    finally:
        conn.close()
    issue: Path = paths.issues_dir / f"{today}.md"
    if issue.exists():
        issue.unlink()
        log.info("removed %s", issue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
