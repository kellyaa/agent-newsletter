"""Tests for backfill.py pure helper functions.

Excludes: find_pre_run_commit() and setup_sandbox() — these depend on
subprocess/git and live filesystem; they are marked pragma: no cover in
terms of unit testing scope. Also excludes rank_with_optional_llm() and
run_writer_for_date() which require LLM mocks outside our scope here.

Covers:
  - age_out_for_synthetic_date()
  - apply_published_to_live() (via bind_db_to_sandbox())
  - bind_db_to_sandbox() — the module-level patch mechanism

Note: build_candidates_snapshot() and persist_decisions() were removed in
PR #142. Their logic is now in candidates.load_candidates_from_db() and
rank.persist() respectively, tested in test_rank_persist.py and the
candidates module tests.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    import db as db_mod
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def db_path(tmp_path):
    import db as db_mod
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p


def _insert_candidate(
    conn,
    item_id: str,
    section: str = "papers",
    score: int | None = 8,
    tags: list | None = None,
    why: str = "good",
    times_competed: int = 0,
    published_at: str = "2026-06-25T00:00:00Z",
    status: str = "candidate",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, fetched_at,
            status, section, score, tags, why,
            first_seen_date, last_seen_date, appearances, times_competed,
            published_at
        ) VALUES (
            ?, 'arxiv:x', 'https://a.com/' || ?, 'https://a.com/' || ?,
            'Title', '2026-06-25T00:00:00Z',
            ?, ?, ?, ?, ?,
            '2026-06-25', '2026-06-25', 1, ?,
            ?
        )
        """,
        (item_id, item_id, item_id,
         status, section, score, json.dumps(tags or []), why,
         times_competed, published_at),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# age_out_for_synthetic_date()
# ---------------------------------------------------------------------------

class TestAgeOutForSyntheticDate:
    def test_drops_papers_exceeding_competition_cap(self, db):
        from backfill import age_out_for_synthetic_date
        from prefilter import PAPER_POOL_MAX_COMPETES

        # Insert a paper that has hit the competition cap
        _insert_candidate(db, "over-cap", section="papers",
                          times_competed=PAPER_POOL_MAX_COMPETES)
        count = age_out_for_synthetic_date(db, "2026-07-01")
        assert count == 1
        row = db.execute("SELECT status FROM items WHERE id='over-cap'").fetchone()
        assert row["status"] == "dropped"

    def test_drops_papers_too_old(self, db):
        from backfill import age_out_for_synthetic_date
        from prefilter import PAPER_POOL_MAX_AGE_DAYS

        # Published much older than PAPER_POOL_MAX_AGE_DAYS before the synthetic date
        very_old = "2026-05-01T00:00:00Z"
        _insert_candidate(db, "old-paper", section="papers",
                          times_competed=0, published_at=very_old)
        count = age_out_for_synthetic_date(db, "2026-07-01")
        assert count == 1
        row = db.execute("SELECT status FROM items WHERE id='old-paper'").fetchone()
        assert row["status"] == "dropped"

    def test_keeps_fresh_papers_below_cap(self, db):
        from backfill import age_out_for_synthetic_date
        from prefilter import PAPER_POOL_MAX_COMPETES

        # Fresh paper, below competition cap
        _insert_candidate(db, "fresh", section="papers",
                          times_competed=PAPER_POOL_MAX_COMPETES - 1,
                          published_at="2026-06-29T00:00:00Z")
        count = age_out_for_synthetic_date(db, "2026-07-01")
        assert count == 0
        row = db.execute("SELECT status FROM items WHERE id='fresh'").fetchone()
        assert row["status"] == "candidate"

    def test_does_not_affect_non_papers_sections(self, db):
        from backfill import age_out_for_synthetic_date
        from prefilter import PAPER_POOL_MAX_COMPETES

        # A news item with exhausted competitions — should NOT be dropped by this function
        # (news/blogs don't live in the candidate pool across days, but if they somehow did)
        _insert_candidate(db, "news-item", section="news",
                          times_competed=PAPER_POOL_MAX_COMPETES + 5)
        count = age_out_for_synthetic_date(db, "2026-07-01")
        assert count == 0  # age-out only targets section='papers'

    def test_does_not_affect_non_candidate_statuses(self, db):
        from backfill import age_out_for_synthetic_date
        from prefilter import PAPER_POOL_MAX_COMPETES

        _insert_candidate(db, "featured", section="papers",
                          times_competed=PAPER_POOL_MAX_COMPETES + 1,
                          status="featured")
        count = age_out_for_synthetic_date(db, "2026-07-01")
        assert count == 0  # only 'candidate' rows are affected

    def test_returns_zero_on_empty_db(self, db):
        from backfill import age_out_for_synthetic_date
        assert age_out_for_synthetic_date(db, "2026-07-01") == 0

    def test_returns_count_of_dropped_rows(self, db):
        from backfill import age_out_for_synthetic_date
        from prefilter import PAPER_POOL_MAX_COMPETES

        for i in range(3):
            _insert_candidate(db, f"over-{i}", section="papers",
                              times_competed=PAPER_POOL_MAX_COMPETES)
        count = age_out_for_synthetic_date(db, "2026-07-01")
        assert count == 3



# ---------------------------------------------------------------------------
# apply_published_to_live() and bind_db_to_sandbox()
# ---------------------------------------------------------------------------

class TestApplyPublishedToLive:
    def test_raises_if_bind_not_called_first(self):
        """apply_published_to_live requires bind_db_to_sandbox to be called first."""
        import backfill as backfill_mod
        # Save and reset the module-level globals
        orig_connect = backfill_mod._LIVE_CONNECT
        orig_path = backfill_mod._LIVE_DB_PATH
        backfill_mod._LIVE_CONNECT = None
        backfill_mod._LIVE_DB_PATH = None
        try:
            with pytest.raises(RuntimeError, match="bind_db_to_sandbox"):
                backfill_mod.apply_published_to_live(["some-id"])
        finally:
            backfill_mod._LIVE_CONNECT = orig_connect
            backfill_mod._LIVE_DB_PATH = orig_path

    def test_promotes_candidate_ids_to_published(self, db_path):
        import db as db_mod
        import backfill as backfill_mod

        # Set up a live DB with candidate items
        conn = db_mod.connect(db_path)
        for i in range(3):
            conn.execute(
                """
                INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                    status, first_seen_date, last_seen_date, appearances)
                VALUES (?, 'rss:x', ?, ?, 'T', '2026-06-25T00:00:00Z',
                        'candidate', '2026-06-25', '2026-06-25', 1)
                """,
                (f"item-{i}", f"https://a.com/{i}", f"https://a.com/{i}"),
            )
        conn.commit()
        conn.close()

        # Manually wire up the backfill module's live pointers
        orig_connect = backfill_mod._LIVE_CONNECT
        orig_path = backfill_mod._LIVE_DB_PATH
        backfill_mod._LIVE_CONNECT = db_mod.connect
        backfill_mod._LIVE_DB_PATH = db_path
        try:
            promoted = backfill_mod.apply_published_to_live(["item-0", "item-1"])
        finally:
            backfill_mod._LIVE_CONNECT = orig_connect
            backfill_mod._LIVE_DB_PATH = orig_path

        assert promoted == 2
        conn = db_mod.connect(db_path)
        statuses = {
            r["id"]: r["status"]
            for r in conn.execute("SELECT id, status FROM items").fetchall()
        }
        conn.close()
        assert statuses["item-0"] == "published"
        assert statuses["item-1"] == "published"
        assert statuses["item-2"] == "candidate"  # not in the list

    def test_idempotent_already_published(self, db_path):
        """Re-running with already-published IDs is a no-op (gated on status='candidate')."""
        import db as db_mod
        import backfill as backfill_mod

        conn = db_mod.connect(db_path)
        conn.execute(
            """
            INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
                status, first_seen_date, last_seen_date, appearances)
            VALUES ('pub-1', 'rss:x', 'https://a.com/pub', 'https://a.com/pub',
                    'T', '2026-06-25T00:00:00Z', 'published',
                    '2026-06-25', '2026-06-25', 1)
            """
        )
        conn.commit()
        conn.close()

        orig_connect = backfill_mod._LIVE_CONNECT
        orig_path = backfill_mod._LIVE_DB_PATH
        backfill_mod._LIVE_CONNECT = db_mod.connect
        backfill_mod._LIVE_DB_PATH = db_path
        try:
            promoted = backfill_mod.apply_published_to_live(["pub-1"])
        finally:
            backfill_mod._LIVE_CONNECT = orig_connect
            backfill_mod._LIVE_DB_PATH = orig_path

        assert promoted == 0  # already published, not re-promoted


class TestBindDbToSandbox:
    def test_patches_db_connect_to_sandbox(self, tmp_path):
        """bind_db_to_sandbox redirects db.connect to the sandbox path."""
        import db as db_mod
        import backfill as backfill_mod

        sandbox_db = tmp_path / "sandbox.db"
        db_mod.init_db(sandbox_db)

        # Save original state
        orig_connect = db_mod.connect
        orig_init_db = db_mod.init_db
        orig_db_path = db_mod.DB_PATH
        orig_live_connect = backfill_mod._LIVE_CONNECT
        orig_live_path = backfill_mod._LIVE_DB_PATH

        try:
            backfill_mod.bind_db_to_sandbox(sandbox_db)
            # After binding, db_mod.DB_PATH should point to the sandbox
            assert db_mod.DB_PATH == sandbox_db
            # The patched connect should open the sandbox DB
            conn = db_mod.connect()
            # We can execute schema queries — this confirms it's a real SQLite conn
            conn.execute("SELECT 1").fetchone()
            conn.close()
        finally:
            # Restore original state to avoid polluting other tests
            db_mod.connect = orig_connect
            db_mod.init_db = orig_init_db
            db_mod.DB_PATH = orig_db_path
            backfill_mod._LIVE_CONNECT = orig_live_connect
            backfill_mod._LIVE_DB_PATH = orig_live_path
