"""Report sources that have produced no items for days (issue #572).

``fetch`` records one ``source_fetch_outcomes`` row per source per run. A feed
that returns nothing without raising looks the same as a quiet one in the run
log, so this reads those rows and prints one line per dead source:

    DEAD: <slug>/<label> no items for N days

A source is dead when it is still listed in the edition's ``sources.yaml``,
every run recorded in the last N days saw zero items or failed, and its first
recorded run is at least N days old (a new source gets a grace period equal to
its threshold). A source with no rows in the window is not reported; a skipped
day is ``missed_days.py``'s concern. The label is ``<family>/<id>``, or
``gh/<owner>/<repo>`` for GitHub releases, as in the fetch log.

N defaults to 7. An optional positive-int ``dead_after_days`` on a
``sources.yaml`` entry overrides it for slow publishers; an invalid value is
logged and ignored, as ``recency_days`` is. The outcome table keeps 90 days, so
a threshold above that can never fire.

Editions with no state.db or no outcome table yet are skipped, as are paused
editions. Read-only: the state DBs are opened with ``mode=ro`` and never
created. Output is plain stdout lines, so it works on hosts without
``osascript``.

Exit codes: 0 = no dead sources, 1 = at least one, 2 = bad usage.

Usage:
    scripts/dead_sources.py [--days N] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

import edition
import logsetup
from db import content_root, load_source_fetch_outcomes
from models import EditionStatus, SourceFamily, is_positive_int, source_label

log = logging.getLogger("dead_sources")

DEFAULT_DAYS = 7


def configured_thresholds(sources_file: Path, default: int) -> dict[str, int] | None:
    """``{label: dead_after_days}`` for every source in `sources_file`.

    None when the file is missing or unreadable. Labels come from
    ``models.source_label``, the same function fetch records them with.
    """
    try:
        data = yaml.safe_load(sources_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    out: dict[str, int] = {}
    for family in SourceFamily:
        entries = data.get(family.value)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            label = source_label(family, entry)
            value = entry.get("dead_after_days")
            if value is not None and not is_positive_int(value):
                log.warning("source %s: invalid dead_after_days %r (must be positive int); ignoring",
                            label, value)
                value = None
            out[label] = default if value is None else value
    return out


def fetch_outcomes(db_file: Path) -> dict[str, list[tuple[str, bool]]]:
    """``{label: [(run_date, produced_items), ...]}``; empty if the table is missing."""
    out: dict[str, list[tuple[str, bool]]] = {}
    for label, run_date, seen, error in load_source_fetch_outcomes(db_file):
        out.setdefault(label, []).append((run_date, error is None and seen > 0))
    return out


def is_dead(rows: list[tuple[str, bool]], today: date, days: int) -> bool:
    """True if every run in the last `days` days (today included) produced nothing."""
    start = (today - timedelta(days=days - 1)).isoformat()
    window = [ok for d, ok in rows if start <= d <= today.isoformat()]
    return bool(window) and not any(window) and min(d for d, _ in rows) <= start


def find_dead(root: Path, today: date, default_days: int) -> list[tuple[str, str, int]]:
    """``(slug, label, days)`` for every dead source of every draft/live edition."""
    out: list[tuple[str, str, int]] = []
    for slug, ed, _err in edition.discover():
        if ed is None or ed.status == EditionStatus.PAUSED:
            continue
        db_file = edition.runtime_paths(root, slug).state_db
        if not db_file.exists():
            continue
        thresholds = configured_thresholds(ed.paths().sources, default_days)
        if thresholds is None:
            continue
        outcomes = fetch_outcomes(db_file)
        for label, days in thresholds.items():
            if label in outcomes and is_dead(outcomes[label], today, days):
                out.append((slug, label, days))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"default dead-after threshold in days (default {DEFAULT_DAYS})")
    ap.add_argument("--today", type=date.fromisoformat, default=None,
                    help="treat this date as today (default: the local date)")
    args = ap.parse_args(argv)
    if args.days < 1:
        ap.error("--days must be at least 1")

    dead = find_dead(content_root(), args.today or date.today(), args.days)
    for slug, label, days in dead:
        print(f"DEAD: {slug}/{label} no items for {days} days")
    return 1 if dead else 0


if __name__ == "__main__":
    logsetup.configure()
    sys.exit(main())
