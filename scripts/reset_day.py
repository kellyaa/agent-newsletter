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
import os
import sys
from datetime import datetime
from pathlib import Path

import edition
from db import CONTENT_ROOT, connect

# Keep in sync with fetch.FETCH_DONE_KEY (not imported: fetch pulls in httpx etc.).
FETCH_DONE_KEY = "fetch_completed_date"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("reset_day")


def refetch(conn, today: str) -> int:
    # Clear fetch's completion marker too: deleting first_seen_date rows alone
    # leaves older items whose last_seen_date was bumped to today, which made
    # fetch skip and the write stage overwrite the live issue (#321).
    conn.execute("DELETE FROM meta WHERE key = ?", (FETCH_DONE_KEY,))
    cur = conn.execute("DELETE FROM items WHERE first_seen_date = ?", (today,))
    conn.commit()
    return cur.rowcount or 0


def force_reset(conn, today: str) -> None:
    # Leave 'new' alone (prefilter handles those naturally) and leave 'dropped'
    # alone (deliberate negatives we don't want to re-evaluate). Match by
    # first_seen_date: last_seen_date matches any prior-day published item that
    # today's feeds re-surfaced (fetch bumps last_seen_date on conflict), which
    # would re-promote already-published items into today's issue.
    conn.execute(
        "UPDATE items SET status = 'candidate', score = NULL, tags = NULL, why = NULL "
        "WHERE status IN ('featured', 'appendix', 'published') AND first_seen_date = ?",
        (today,),
    )
    conn.execute("DELETE FROM runs WHERE date = ?", (today,))
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args(argv)

    paths = edition.runtime_paths(CONTENT_ROOT)
    today = os.environ.get("RUN_DATE") or datetime.now().date().isoformat()
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
