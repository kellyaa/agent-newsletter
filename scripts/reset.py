"""Pipeline state reset helpers for run.sh --force and --refetch modes.

Replaces the inline Python heredocs in run.sh with a proper module that:
  - Uses db.connect() for WAL mode + busy_timeout
  - Uses Status enum for status literals
  - Accepts --force and --refetch flags
  - Uses timezone-aware datetime for consistency with prefilter.py

Usage (from run.sh):
  uv run python scripts/reset.py --force
  uv run python scripts/reset.py --refetch
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from db import CONTENT_ROOT, connect, init_db
from models import Status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("reset")

ISSUES_DIR = CONTENT_ROOT / "site" / "src" / "content" / "issues"


def today_iso() -> str:
    """Return today's date in ISO format, UTC-based for consistency."""
    return datetime.now(timezone.utc).date().isoformat()


def refetch(today: str) -> int:
    """Delete all items first seen today so fetch.py re-ingests them."""
    init_db()
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM items WHERE first_seen_date = ?", (today,)
        )
        conn.commit()
        deleted = cur.rowcount or 0
        log.info("refetch: deleted %d items first seen on %s", deleted, today)
        return deleted
    finally:
        conn.close()


def force_reset(today: str) -> None:
    """Reset today's post-fetch state so rank/write can re-run.

    - Moves today's featured/appendix/published items back to 'candidate'
    - Clears their scores so the ranker re-evaluates
    - Deletes today's runs row
    - Removes today's issue file if it exists
    """
    init_db()
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE items SET status = ?, score = NULL, tags = NULL, why = NULL "
            "WHERE status IN (?, ?, ?) AND first_seen_date = ?",
            (
                Status.CANDIDATE,
                Status.FEATURED,
                Status.APPENDIX,
                Status.PUBLISHED,
                today,
            ),
        )
        conn.commit()
        reset_count = cur.rowcount or 0
        log.info("force: reset %d items to status='%s'", reset_count, Status.CANDIDATE)

        cur = conn.execute("DELETE FROM runs WHERE date = ?", (today,))
        conn.commit()
        if cur.rowcount:
            log.info("force: deleted runs row for %s", today)
    finally:
        conn.close()

    # Drop today's issue file so write.py runs.
    issue_path = ISSUES_DIR / f"{today}.md"
    if issue_path.exists():
        issue_path.unlink()
        log.info("force: removed %s", issue_path)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Reset pipeline state for --force / --refetch re-runs.",
    )
    p.add_argument(
        "--force", action="store_true",
        help="Reset today's post-fetch state (featured/appendix/published → candidate).",
    )
    p.add_argument(
        "--refetch", action="store_true",
        help="Delete today's fetched items so fetch.py re-ingests. Implies --force.",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.force and not args.refetch:
        log.error("must pass --force or --refetch")
        return 2

    today = today_iso()

    # --refetch implies --force (same as run.sh behavior)
    if args.refetch:
        refetch(today)
        force_reset(today)
    elif args.force:
        force_reset(today)

    return 0


if __name__ == "__main__":
    sys.exit(main())
