"""Report days on which an edition's run never started (issue #519).

``run_attempts`` records one row per ``run.sh`` invocation, failures included,
so a failed day is visible and ``run.sh --failed`` can re-run it. A day on which
``run.sh`` never started leaves *no* row at all, so nothing flags it and there is
nothing to re-run. This script finds those gaps.

For each draft/live edition it looks at the last ``--days`` days before today
(today's run may simply not have happened yet) and prints one line per date
that has no ``run_attempts`` row:

    MISSED: <slug> <YYYY-MM-DD>

Dates before the edition's first recorded attempt are ignored, so a new edition
is not reported as having missed days it did not exist for. Editions with no
state.db yet are skipped. Paused editions are not checked.

Read-only: the state DBs are opened with ``mode=ro`` and never created. Output
is plain stdout lines, so it works on hosts without ``osascript``.

Exit codes: 0 = no gaps, 1 = at least one gap, 2 = bad usage.

Usage:
    scripts/missed_days.py [--days N] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import edition
from db import content_root

DEFAULT_DAYS = 7


def attempt_dates(db_file: Path) -> set[str]:
    """Every ``run_attempts.date`` in `db_file`; empty if it has no such table."""
    conn = sqlite3.connect(f"{db_file.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT date FROM run_attempts")}
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()


def missed_dates(recorded: set[str], today: date, days: int) -> list[str]:
    """Dates in the `days` days before `today` with no row, from the first row on."""
    if not recorded:
        return []
    first = min(recorded)
    window = (today - timedelta(days=n) for n in range(days, 0, -1))
    return [d.isoformat() for d in window if d.isoformat() >= first and d.isoformat() not in recorded]


def find_missed(root: Path, today: date, days: int) -> list[tuple[str, str]]:
    """``(slug, date)`` for every missed day of every draft/live edition."""
    out: list[tuple[str, str]] = []
    for slug, ed, _err in edition.discover():
        if ed is not None and ed.status == "paused":
            continue
        db_file = edition.runtime_paths(root, slug).state_db
        if not db_file.exists():
            continue
        out.extend((slug, d) for d in missed_dates(attempt_dates(db_file), today, days))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"how many days before today to check (default {DEFAULT_DAYS})")
    ap.add_argument("--today", type=date.fromisoformat, default=None,
                    help="treat this date as today (default: the local date)")
    args = ap.parse_args(argv)
    if args.days < 1:
        ap.error("--days must be at least 1")

    missed = find_missed(content_root(), args.today or date.today(), args.days)
    for slug, d in missed:
        print(f"MISSED: {slug} {d}")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
