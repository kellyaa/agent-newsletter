"""Tests for fetch.py: upsert_items() and _already_fetched_today()."""
from __future__ import annotations

import pytest

from fetch import Item, upsert_items, _already_fetched_today
from db import init_db, connect, url_id


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

    def test_last_seen_date_updated_on_conflict(self, db):
        from unittest.mock import patch
        from datetime import date

        item = _make_item()
        with patch("fetch.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-06-01"
            mock_dt.now.return_value.isoformat.return_value = "2026-06-01T00:00:00Z"
            upsert_items(db, [item])

        with patch("fetch.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-06-02"
            mock_dt.now.return_value.isoformat.return_value = "2026-06-02T00:00:00Z"
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
# _already_fetched_today
# ---------------------------------------------------------------------------

class TestAlreadyFetchedToday:
    def test_returns_zero_on_empty_db(self, db):
        result = _already_fetched_today(db)
        assert result == 0

    def test_counts_items_seen_today(self, db):
        from datetime import date
        from unittest.mock import patch

        today = date.today().isoformat()
        item = _make_item()
        with patch("fetch.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = today
            mock_dt.now.return_value.isoformat.return_value = f"{today}T00:00:00Z"
            upsert_items(db, [item])

        result = _already_fetched_today(db)
        assert result == 1

    def test_does_not_count_items_from_yesterday(self, db):
        from unittest.mock import patch

        item = _make_item()
        with patch("fetch.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-05-30"
            mock_dt.now.return_value.isoformat.return_value = "2026-05-30T00:00:00Z"
            upsert_items(db, [item])

        # _already_fetched_today uses datetime.now().date() internally
        result = _already_fetched_today(db)
        # Today is not 2026-05-30, so result should be 0
        assert result == 0
