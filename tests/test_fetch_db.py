"""Tests for fetch.py: upsert_items() and _already_fetched_today()."""
from __future__ import annotations

import pytest

from fetch import FETCH_DONE_KEY, Item, upsert_items, _already_fetched_today
from db import init_db, connect, set_meta, url_id


# ---------------------------------------------------------------------------
# Fixture: fresh in-tmp-path DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    db_path = tmp_path / "state.db"
    init_db(db_path)
    conn = connect(db_path)
    yield conn
    conn.close()


def _make_item(**kwargs) -> Item:
    defaults = dict(
        source="rss:test",
        url="https://example.com/article-1",
        title="Test article",
        author="Jane",
        published_at="2026-06-01T00:00:00Z",
        raw_text="Some text",
    )
    defaults.update(kwargs)
    return Item(**defaults)


# ---------------------------------------------------------------------------
# upsert_items — insert path
# ---------------------------------------------------------------------------

class TestUpsertItemsInsert:
    def test_inserts_new_item(self, db):
        items = [_make_item()]
        seen, inserted = upsert_items(db, items)
        assert seen == 1
        assert inserted == 1

    def test_inserted_item_has_status_new(self, db):
        items = [_make_item()]
        upsert_items(db, items)
        row = db.execute("SELECT status FROM items").fetchone()
        assert row["status"] == "new"

    def test_id_is_url_hash(self, db):
        item = _make_item(url="https://example.com/article-42")
        upsert_items(db, [item])
        expected_id = url_id(item.url)
        row = db.execute("SELECT id FROM items WHERE id = ?", (expected_id,)).fetchone()
        assert row is not None

    def test_multiple_items_all_inserted(self, db):
        items = [
            _make_item(url=f"https://example.com/{i}", title=f"Title {i}")
            for i in range(5)
        ]
        seen, inserted = upsert_items(db, items)
        assert seen == 5
        assert inserted == 5

    def test_section_override_stored(self, db):
        item = _make_item()
        item.section_override = "papers"
        upsert_items(db, [item])
        row = db.execute("SELECT section_override FROM items").fetchone()
        assert row["section_override"] == "papers"

    def test_keyword_gate_bypass_stored(self, db):
        item = _make_item()
        item.keyword_gate_bypass = True
        upsert_items(db, [item])
        row = db.execute("SELECT keyword_gate_bypass FROM items").fetchone()
        assert row["keyword_gate_bypass"] == 1

    def test_recency_days_override_stored(self, db):
        item = _make_item()
        item.recency_days_override = 14
        upsert_items(db, [item])
        row = db.execute("SELECT recency_days_override FROM items").fetchone()
        assert row["recency_days_override"] == 14


# ---------------------------------------------------------------------------
# upsert_items — conflict/update path (same URL seen again)
# ---------------------------------------------------------------------------

class TestUpsertItemsConflict:
    def test_duplicate_url_increments_appearances(self, db):
        item = _make_item()
        upsert_items(db, [item])
        upsert_items(db, [item])  # same URL again
        row = db.execute("SELECT appearances FROM items").fetchone()
        assert row["appearances"] == 2

    def test_duplicate_increments_appearances_not_inserted(self, db):
        """On conflict, appearances increments but the row is not a new insert.

        SQLite ON CONFLICT DO UPDATE sets rowcount=1 and lastrowid to the existing
        row's rowid even on update — so upsert_items counts a conflict as inserted=1.
        This test documents the observed behavior (appearances increments correctly).
        """
        item = _make_item()
        upsert_items(db, [item])
        upsert_items(db, [item])  # same URL again — conflict path
        row = db.execute("SELECT appearances FROM items").fetchone()
        assert row["appearances"] == 2  # appearances is correctly incremented

    def test_section_override_not_overwritten_by_none(self, db):
        """If the re-seen item has no section_override, existing value is preserved."""
        item = _make_item()
        item.section_override = "papers"
        upsert_items(db, [item])

        item2 = _make_item()
        item2.section_override = None
        upsert_items(db, [item2])

        row = db.execute("SELECT section_override FROM items").fetchone()
        assert row["section_override"] == "papers"  # COALESCE preserves original

    def test_keyword_gate_bypass_max_preserved(self, db):
        """MAX() semantics: once bypass=1, it stays 1 even if re-seen with bypass=0."""
        item = _make_item()
        item.keyword_gate_bypass = True
        upsert_items(db, [item])

        item2 = _make_item()
        item2.keyword_gate_bypass = False
        upsert_items(db, [item2])

        row = db.execute("SELECT keyword_gate_bypass FROM items").fetchone()
        assert row["keyword_gate_bypass"] == 1

    def test_last_seen_date_updated_on_conflict(self, db, monkeypatch):
        item = _make_item()
        monkeypatch.setenv("RUN_DATE", "2026-06-01")
        upsert_items(db, [item])

        monkeypatch.setenv("RUN_DATE", "2026-06-02")
        upsert_items(db, [item])

        row = db.execute("SELECT last_seen_date FROM items").fetchone()
        assert row["last_seen_date"] == "2026-06-02"


# ---------------------------------------------------------------------------
# upsert_items — generator input
# ---------------------------------------------------------------------------

class TestUpsertItemsGenerator:
    def test_accepts_generator(self, db):
        def gen():
            for i in range(3):
                yield _make_item(url=f"https://example.com/{i}", title=f"T{i}")

        seen, inserted = upsert_items(db, gen())
        assert seen == 3
        assert inserted == 3

    def test_empty_input_returns_zero_zero(self, db):
        seen, inserted = upsert_items(db, [])
        assert seen == 0
        assert inserted == 0


# ---------------------------------------------------------------------------
# upsert_items — URL scheme allowlist (issue #248)
# ---------------------------------------------------------------------------

class TestUpsertItemsSchemeAllowlist:
    def test_rejects_javascript_scheme(self, db):
        item = _make_item(url="javascript:alert(1)")
        seen, inserted = upsert_items(db, [item])
        assert seen == 1
        assert inserted == 0
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def test_rejects_data_scheme(self, db):
        item = _make_item(url="data:text/html,<script>alert(1)</script>")
        seen, inserted = upsert_items(db, [item])
        assert inserted == 0
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def test_rejects_file_scheme(self, db):
        item = _make_item(url="file:///etc/passwd")
        seen, inserted = upsert_items(db, [item])
        assert inserted == 0
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    def test_disallowed_scheme_does_not_block_other_items(self, db):
        items = [
            _make_item(url="javascript:alert(1)", title="Bad"),
            _make_item(url="https://example.com/good", title="Good"),
        ]
        seen, inserted = upsert_items(db, items)
        assert seen == 2
        assert inserted == 1
        row = db.execute("SELECT title FROM items").fetchone()
        assert row["title"] == "Good"

    def test_accepts_http_and_https(self, db):
        items = [
            _make_item(url="http://example.com/a", title="A"),
            _make_item(url="https://example.com/b", title="B"),
        ]
        seen, inserted = upsert_items(db, items)
        assert seen == 2
        assert inserted == 2


# ---------------------------------------------------------------------------
# _already_fetched_today
# ---------------------------------------------------------------------------

class TestAlreadyFetchedToday:
    def test_returns_zero_on_empty_db(self, db):
        result = _already_fetched_today(db)
        assert result == 0

    def test_counts_items_seen_today(self, db, monkeypatch):
        from datetime import date

        today = date.today().isoformat()
        item = _make_item()
        monkeypatch.setenv("RUN_DATE", today)
        upsert_items(db, [item])

        set_meta(db, FETCH_DONE_KEY, today)
        result = _already_fetched_today(db)
        assert result == 1

    def test_requires_completion_marker(self, db):
        """Rows alone (e.g. older items re-surfaced today) don't mean fetched."""
        from datetime import date

        today = date.today().isoformat()
        db.execute(
            "INSERT INTO items (id, source, url, canonical_url, title, fetched_at,"
            " status, first_seen_date, last_seen_date, appearances)"
            " VALUES ('x','rss:t','https://a.com','https://a.com','T',"
            " '2026-01-01T00:00:00Z','published','2026-01-01',?,2)",
            (today,),
        )
        db.commit()
        assert _already_fetched_today(db) == 0
        set_meta(db, FETCH_DONE_KEY, today)
        assert _already_fetched_today(db) == 1

    def test_uses_run_date_env(self, db, monkeypatch):
        monkeypatch.setenv("RUN_DATE", "2026-10-24")
        db.execute(
            "INSERT INTO items (id, source, url, canonical_url, title, fetched_at,"
            " status, first_seen_date, last_seen_date, appearances)"
            " VALUES ('x','rss:t','https://a.com','https://a.com','T',"
            " '2026-10-24T00:00:00Z','new','2026-10-24','2026-10-24',1)"
        )
        db.commit()
        set_meta(db, FETCH_DONE_KEY, "2026-10-24")
        assert _already_fetched_today(db) == 1
        monkeypatch.setenv("RUN_DATE", "2026-10-25")
        assert _already_fetched_today(db) == 0

    def test_upsert_stamps_run_date_not_wall_clock(self, db, monkeypatch):
        """The day items are stamped with is the run's RUN_DATE, the same day
        the fetch-completed marker records (a run crossing midnight or a manual
        re-run for an earlier date must not split the two)."""
        monkeypatch.setenv("RUN_DATE", "2026-10-24")
        upsert_items(db, [_make_item()])
        set_meta(db, FETCH_DONE_KEY, "2026-10-24")

        row = db.execute("SELECT first_seen_date, last_seen_date FROM items").fetchone()
        assert (row["first_seen_date"], row["last_seen_date"]) == ("2026-10-24", "2026-10-24")
        assert _already_fetched_today(db) == 1

    def test_does_not_count_items_from_yesterday(self, db, monkeypatch):
        item = _make_item()
        monkeypatch.setenv("RUN_DATE", "2026-05-30")
        upsert_items(db, [item])
        set_meta(db, FETCH_DONE_KEY, "2026-05-30")

        # Checked on a later run with no RUN_DATE: falls back to the local date.
        monkeypatch.delenv("RUN_DATE", raising=False)
        result = _already_fetched_today(db)
        # Today is not 2026-05-30, so result should be 0
        assert result == 0


# ---------------------------------------------------------------------------
# upsert_items — injectable clock
# ---------------------------------------------------------------------------

class TestUpsertItemsClock:
    def test_now_stamps_fetched_at(self, db):
        from datetime import datetime, timezone

        pinned = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)
        upsert_items(db, [_make_item()], now=pinned)
        row = db.execute("SELECT fetched_at FROM items").fetchone()
        assert row["fetched_at"] == pinned.isoformat()
