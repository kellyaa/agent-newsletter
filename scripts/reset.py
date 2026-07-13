"""Pipeline reset helpers — used by run.sh --force and --refetch.

Consolidates the inline Python/SQL that run.sh previously embedded in
heredocs. All database access goes through db.connect() to respect WAL mode,
pragmas, and connection configuration. Status literals are defined once as
constants — any future rename only changes one place.

Usage from run.sh:
  uv run python scripts/reset.py refetch
  uv run python scripts/reset.py force
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from db import CONTENT_ROOT, connect, init_db

# Status constants — single source of truth for pipeline status strings.
STATUS_NEW = "new"
STATUS_CANDIDATE = "candidate"
STATUS_FEATURED = "featured"
STATUS_APPENDIX = "appendix"
STATUS_PUBLISHED = "published"
STATUS_DROPPED = "dropped"

# Statuses that --force resets back to candidate (downstream of fetch).
FORCE_RESET_STATUSES = (STATUS_FEATURED, STATUS_APPENDIX, STATUS_PUBLISHED)


def refetch(today: str | None = None) -> int:
    """Delete today's fetched items so fetch runs again from scratch.

    Equivalent to the old inline heredoc in run.sh's --refetch block.
    Returns the number of rows deleted.
    """
    init_db()
    if today is None:
        today = datetime.now().date().isoformat()
    conn = connect()
    try:
        cur = conn.execute(
            "DELETE FROM items WHERE first_seen_date = ?", (today,)
        )
        conn.commit()
        deleted = cur.rowcount or 0
        print(f"deleted {deleted} items first seen on {today}")
        return deleted
    finally:
        conn.close()


def force(today: str | None = None) -> dict[str, int]:
    """Reset today's post-fetch state so rank/write can re-run.

    Sends today's processed items back to 'candidate'; deletes today's
    runs row; removes today's issue file. Returns a summary dict.

    Equivalent to the old inline heredoc in run.sh's --force block.
    """
    init_db()
    if today is None:
        today = datetime.now().date().isoformat()
    conn = connect()
    try:
        cur = conn.execute(
            "UPDATE items SET status = ?, score = NULL, tags = NULL, why = NULL "
            "WHERE status IN (?, ?, ?) AND first_seen_date = ?",
            (STATUS_CANDIDATE, *FORCE_RESET_STATUSES, today),
        )
        reset_count = cur.rowcount or 0

        cur2 = conn.execute("DELETE FROM runs WHERE date = ?", (today,))
        runs_deleted = cur2.rowcount or 0
        conn.commit()
    finally:
        conn.close()

    # Drop today's issue file so write.py runs.
    issue = CONTENT_ROOT / "site" / "src" / "content" / "issues" / f"{today}.md"
    issue_removed = False
    if issue.exists():
        issue.unlink()
        issue_removed = True
        print(f"removed {issue}")

    return {
        "items_reset": reset_count,
        "runs_deleted": runs_deleted,
        "issue_removed": issue_removed,
    }


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("refetch", "force"):
        print("usage: python scripts/reset.py {refetch|force}", file=sys.stderr)
        return 2

    command = sys.argv[1]
    if command == "refetch":
        refetch()
    elif command == "force":
        force()
    return 0


if __name__ == "__main__":
    sys.exit(main())
