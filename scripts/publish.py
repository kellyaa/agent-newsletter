"""Finalize the day's run.

The Astro site's content collection is at site/src/content/issues/. write.py
emits a YAML-frontmatter+body MD file there. This script:
  - Verifies the issue file exists and is non-trivial.
  - Promotes today's featured + appendix items to status='published'.
  - Records a row in the `runs` table.

URL hallucination is no longer possible: write.py splices URLs from the DB
into the frontmatter, so the LLM has no opportunity to invent them. The
Astro build itself will fail if the frontmatter doesn't match the schema in
site/src/content.config.ts, providing a second line of defense.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from db import CONTENT_ROOT, REPO_ROOT, connect, init_db
from models import Status

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("publish")

ISSUES_DIR = CONTENT_ROOT / "site" / "src" / "content" / "issues"

MIN_FEATURED_FOR_PUBLISH = 1  # tunable; lower = more permissive
MIN_FILE_SIZE_BYTES = 400     # frontmatter+body should comfortably exceed this


def record_run(
    conn,
    today: str,
    featured_counts: dict[str, int],
    appendix_count: int,
    items_considered: int,
) -> None:
    conn.execute(
        """
        INSERT INTO runs (date, items_fetched, items_candidate, items_featured,
                          items_papers, items_news, items_blogs, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            items_fetched = excluded.items_fetched,
            items_featured = excluded.items_featured,
            items_papers = excluded.items_papers,
            items_news = excluded.items_news,
            items_blogs = excluded.items_blogs,
            notes = excluded.notes
        """,
        (
            today,
            items_considered,
            featured_counts.get("papers", 0)
                + featured_counts.get("news", 0)
                + featured_counts.get("blogs", 0)
                + appendix_count,
            sum(featured_counts.values()),
            featured_counts.get("papers", 0),
            featured_counts.get("news", 0),
            featured_counts.get("blogs", 0),
            f"appendix={appendix_count}",
        ),
    )
    conn.commit()


def main() -> int:
    init_db()
    today = datetime.now().date().isoformat()
    issue_path = ISSUES_DIR / f"{today}.md"
    if not issue_path.exists():
        log.error("missing %s — run write.py first", issue_path)
        return 2

    size = issue_path.stat().st_size
    if size < MIN_FILE_SIZE_BYTES:
        log.error("issue file is unreasonably small (%d bytes); refusing to publish", size)
        return 4

    conn = connect()

    # Idempotent skip: if a runs row exists for today AND no featured/appendix
    # items remain in candidate-of-publish state, this run already finished.
    runs_row = conn.execute(
        "SELECT items_featured FROM runs WHERE date = ?", (today,)
    ).fetchone()
    pending = conn.execute(
        f"SELECT COUNT(*) FROM items WHERE status IN ('{Status.FEATURED}', '{Status.APPENDIX}')"
    ).fetchone()[0]
    if runs_row is not None and pending == 0:
        log.info(
            "publish: skip — already published for %s (runs.items_featured=%d)",
            today, runs_row[0] or 0,
        )
        conn.close()
        return 0
    rows = conn.execute(
        f"SELECT section FROM items WHERE status = '{Status.FEATURED}'"
    ).fetchall()
    featured_counts: dict[str, int] = {}
    for r in rows:
        s = r["section"]
        featured_counts[s] = featured_counts.get(s, 0) + 1
    appendix_count = conn.execute(
        f"SELECT COUNT(*) FROM items WHERE status = '{Status.APPENDIX}'"
    ).fetchone()[0]
    items_considered = conn.execute(
        "SELECT COUNT(*) FROM items WHERE last_seen_date = ?", (today,)
    ).fetchone()[0]

    total_featured = sum(featured_counts.values())
    if total_featured < MIN_FEATURED_FOR_PUBLISH and appendix_count == 0:
        log.error(
            "no featured items and no appendix items — refusing to publish empty issue"
        )
        conn.close()
        return 5

    conn.execute(
        f"UPDATE items SET status = '{Status.PUBLISHED}' "
        f"WHERE status IN ('{Status.FEATURED}', '{Status.APPENDIX}')"
    )
    conn.commit()
    log.info(
        "promoted featured=%d (papers=%d news=%d blogs=%d) and appendix=%d to published",
        total_featured,
        featured_counts.get("papers", 0),
        featured_counts.get("news", 0),
        featured_counts.get("blogs", 0),
        appendix_count,
    )

    record_run(conn, today, featured_counts, appendix_count, items_considered)
    conn.close()

    log.info("issue file: %s (%d bytes)", issue_path, size)
    log.info("the Astro build is the next gate; it validates frontmatter against the schema")
    return 0


if __name__ == "__main__":
    sys.exit(main())
