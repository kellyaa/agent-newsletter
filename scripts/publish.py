"""Finalize the day's run.

The Astro site's content collection is at site/src/content/issues/. write.py
emits a YAML-frontmatter+body MD file there. This script:
  - Verifies the issue file exists and is non-trivial.
  - Promotes today's featured + appendix items to status='published'.
  - Records a row in the `runs` table, with token usage and duration when
    run.sh provides them (LLM_USAGE_LOG, RUN_STARTED_AT; #13).

URL hallucination is no longer possible: write.py splices URLs from the DB
into the frontmatter, so the LLM has no opportunity to invent them. The
Astro build itself will fail if the frontmatter doesn't match the schema in
site/src/content.config.ts, providing a second line of defense.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import edition
from db import connect, content_root, init_db, load_stage_stats, run_date
from db import (
    count_featured_by_section,
    count_items_in_status,
    count_items_seen_on,
    load_run_featured_count,
    publish_selected_items,
    upsert_run,
)
from llm import usage_totals
from models import ExitCode, Section, Status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("publish")

def issues_dir() -> Path:
    """The selected edition's issue folder, resolved from the env at call time.

    Reads ``EDITION`` / ``CONTENT_ROOT`` on every call (like :func:`db.db_path`),
    so ``import publish`` needs no edition and never pins a stale one (#446).
    """
    return edition.runtime_paths(content_root()).issues_dir


def __getattr__(name: str):
    # Deprecated alias for issues_dir(), kept for existing readers; resolved
    # lazily so importing this module does not require an edition.
    if name == "ISSUES_DIR":
        return issues_dir()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

MIN_FEATURED_FOR_PUBLISH = 1  # tunable; lower = more permissive
MIN_FILE_SIZE_BYTES = 400     # frontmatter+body should comfortably exceed this


def run_duration_s(started_at: str | None, now: datetime | None = None) -> int | None:
    """Seconds since RUN_STARTED_AT (ISO 8601 UTC, set by run.sh), or None."""
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if start.tzinfo is None:
        # A zone-less timestamp is documented as UTC; subtracting it from an
        # aware ``now`` would otherwise raise TypeError and abort publish.
        start = start.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - start).total_seconds()))


def record_run(
    conn,
    today: str,
    featured_counts: dict[str, int],
    appendix_count: int,
    items_considered: int,
    usage: dict | None = None,
    duration_s: int | None = None,
    stage_stats: dict | None = None,
) -> None:
    notes = f"appendix={appendix_count}"
    if usage is not None:
        notes += (f" llm_calls={usage['calls']} models={usage['models']}"
                  f" tokens_by_model={usage['tokens_by_model']}")
    usage = usage or {}
    stage_stats = stage_stats or {}
    papers = featured_counts.get(Section.PAPERS, 0)
    news = featured_counts.get(Section.NEWS, 0)
    blogs = featured_counts.get(Section.BLOGS, 0)
    upsert_run(
        conn,
        today,
        items_fetched=items_considered,
        items_candidate=papers + news + blogs + appendix_count,
        items_featured=sum(featured_counts.values()),
        items_papers=papers,
        items_news=news,
        items_blogs=blogs,
        notes=notes,
        duration_seconds=duration_s,
        tokens_in=usage.get("tokens_in"),
        tokens_out=usage.get("tokens_out"),
        papers_expired_unjudged=stage_stats.get("papers_expired_unjudged"),
        papers_prerank_cut=stage_stats.get("papers_prerank_cut"),
    )


def main(today: str | None = None) -> int:
    """Publish the issue for ``today`` (``YYYY-MM-DD``; defaults to :func:`db.run_date`).

    ``today`` is injectable so tests and backfills pin the run date instead of
    setting ``RUN_DATE`` in the environment, matching ``write.main(today=...)``
    and fetch/prefilter/rank's ``main(now=...)``.
    """
    init_db()
    if today is None:
        today = run_date()
    issue_path = issues_dir() / f"{today}.md"
    if not issue_path.exists():
        log.error("missing %s — run write.py first", issue_path)
        return ExitCode.MISSING_INPUT

    size = issue_path.stat().st_size
    if size < MIN_FILE_SIZE_BYTES:
        log.error("issue file is unreasonably small (%d bytes); refusing to publish", size)
        return ExitCode.ISSUE_TOO_SMALL

    conn = connect()

    # Idempotent skip: if a runs row exists for today AND no featured/appendix
    # items remain in candidate-of-publish state, this run already finished.
    published_featured = load_run_featured_count(conn, today)
    pending = count_items_in_status(conn, Status.FEATURED, Status.APPENDIX)
    if published_featured is not None and pending == 0:
        log.info(
            "publish: skip — already published for %s (runs.items_featured=%d)",
            today, published_featured,
        )
        conn.close()
        return ExitCode.OK
    featured_counts = count_featured_by_section(conn)
    appendix_count = count_items_in_status(conn, Status.APPENDIX)
    items_considered = count_items_seen_on(conn, today)

    total_featured = sum(featured_counts.values())
    if total_featured < MIN_FEATURED_FOR_PUBLISH and appendix_count == 0:
        log.error(
            "no featured items and no appendix items — refusing to publish empty issue"
        )
        conn.close()
        return ExitCode.EMPTY_ISSUE

    publish_selected_items(conn, today)
    log.info(
        "promoted featured=%d (papers=%d news=%d blogs=%d) and appendix=%d to published",
        total_featured,
        featured_counts.get(Section.PAPERS, 0),
        featured_counts.get(Section.NEWS, 0),
        featured_counts.get(Section.BLOGS, 0),
        appendix_count,
    )

    record_run(
        conn, today, featured_counts, appendix_count, items_considered,
        usage=usage_totals(os.environ.get("LLM_USAGE_LOG")),
        duration_s=run_duration_s(os.environ.get("RUN_STARTED_AT")),
        stage_stats=load_stage_stats(conn, today),
    )
    conn.close()

    log.info("issue file: %s (%d bytes)", issue_path, size)
    log.info("the Astro build is the next gate; it validates frontmatter against the schema")
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
