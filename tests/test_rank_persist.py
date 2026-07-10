"""Tests for rank.py: persist() DB writes and VALID_TAGS filtering."""
from __future__ import annotations

import json
import pytest

from rank import persist, VALID_TAGS


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    import db as db_mod
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    # Insert a candidate item to update
    conn.execute(
        """
        INSERT INTO items (
            id, source, url, canonical_url, title, fetched_at,
            status, section, first_seen_date, last_seen_date, appearances
        ) VALUES (?, 'arxiv:x', 'https://a.com/1', 'https://a.com/1',
                  'Paper title', '2026-06-01T00:00:00Z',
                  'candidate', 'papers', '2026-06-01', '2026-06-01', 1)
        """,
        ("item-1",),
    )
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# persist() — correct DB writes
# ---------------------------------------------------------------------------

class TestPersistWrites:
    def test_updates_status(self, db):
        decisions = {
            "item-1": {"status": "featured", "score": 8, "tags": [], "why": "good", "section": "papers"},
        }
        counts = persist(db, decisions)
        row = db.execute("SELECT status FROM items WHERE id='item-1'").fetchone()
        assert row["status"] == "featured"

    def test_updates_score(self, db):
        decisions = {
            "item-1": {"status": "featured", "score": 9, "tags": [], "why": "top", "section": "papers"},
        }
        persist(db, decisions)
        row = db.execute("SELECT score FROM items WHERE id='item-1'").fetchone()
        assert row["score"] == 9

    def test_updates_why(self, db):
        decisions = {
            "item-1": {"status": "appendix", "score": 5, "tags": [], "why": "relevant", "section": "papers"},
        }
        persist(db, decisions)
        row = db.execute("SELECT why FROM items WHERE id='item-1'").fetchone()
        assert row["why"] == "relevant"

    def test_tags_serialized_as_json(self, db):
        decisions = {
            "item-1": {"status": "featured", "score": 8, "tags": ["evals", "research"], "why": "ok", "section": "papers"},
        }
        persist(db, decisions)
        row = db.execute("SELECT tags FROM items WHERE id='item-1'").fetchone()
        parsed = json.loads(row["tags"])
        assert parsed == ["evals", "research"]

    def test_returns_status_counts(self, db):
        decisions = {
            "item-1": {"status": "featured", "score": 8, "tags": [], "why": "ok", "section": "papers"},
        }
        counts = persist(db, decisions)
        assert counts.get("featured") == 1


# ---------------------------------------------------------------------------
# persist() — VALID_TAGS filtering
# ---------------------------------------------------------------------------

class TestPersistValidTagsFilter:
    def test_invalid_tags_stripped(self, db):
        decisions = {
            "item-1": {
                "status": "featured", "score": 8,
                "tags": ["evals", "INVALID_TAG", "research"],
                "why": "ok", "section": "papers",
            },
        }
        persist(db, decisions)
        row = db.execute("SELECT tags FROM items WHERE id='item-1'").fetchone()
        parsed = json.loads(row["tags"])
        assert "INVALID_TAG" not in parsed
        assert "evals" in parsed
        assert "research" in parsed

    def test_all_invalid_tags_produces_empty_list(self, db):
        decisions = {
            "item-1": {
                "status": "appendix", "score": 5,
                "tags": ["not-a-tag", "also-bad"],
                "why": "ok", "section": "papers",
            },
        }
        persist(db, decisions)
        row = db.execute("SELECT tags FROM items WHERE id='item-1'").fetchone()
        assert json.loads(row["tags"]) == []

    def test_all_valid_tags_pass_through(self, db):
        valid_sample = list(VALID_TAGS)[:3]
        decisions = {
            "item-1": {
                "status": "featured", "score": 8,
                "tags": valid_sample,
                "why": "ok", "section": "papers",
            },
        }
        persist(db, decisions)
        row = db.execute("SELECT tags FROM items WHERE id='item-1'").fetchone()
        parsed = json.loads(row["tags"])
        assert set(parsed) == set(valid_sample)

    def test_empty_tags_list_stored_as_empty_json_array(self, db):
        decisions = {
            "item-1": {
                "status": "dropped", "score": 2,
                "tags": [],
                "why": "low", "section": "papers",
            },
        }
        persist(db, decisions)
        row = db.execute("SELECT tags FROM items WHERE id='item-1'").fetchone()
        assert json.loads(row["tags"]) == []


# ---------------------------------------------------------------------------
# persist() — multiple items
# ---------------------------------------------------------------------------

class TestPersistMultipleItems:
    def test_processes_multiple_items(self, tmp_path):
        import db as db_mod
        db_path = tmp_path / "state.db"
        db_mod.init_db(db_path)
        conn = db_mod.connect(db_path)

        for i in range(3):
            conn.execute(
                """
                INSERT INTO items (
                    id, source, url, canonical_url, title, fetched_at,
                    status, section, first_seen_date, last_seen_date, appearances
                ) VALUES (?, 'rss:x', ?, ?, 'T', '2026-06-01T00:00:00Z',
                          'candidate', 'news', '2026-06-01', '2026-06-01', 1)
                """,
                (f"item-{i}", f"https://a.com/{i}", f"https://a.com/{i}"),
            )
        conn.commit()

        decisions = {
            f"item-{i}": {"status": "featured", "score": 7, "tags": [], "why": "ok", "section": "news"}
            for i in range(3)
        }
        counts = persist(conn, decisions)
        assert counts.get("featured") == 3
        conn.close()
