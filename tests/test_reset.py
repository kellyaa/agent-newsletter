"""Tests for scripts/reset.py: refetch() and force() pipeline reset helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import db as db_mod
import reset as reset_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Provide a fresh DB and patch reset module to use tmp_path."""
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)

    # Patch reset module's imported references to use our temp DB
    monkeypatch.setattr(reset_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(reset_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(reset_mod, "CONTENT_ROOT", tmp_path)

    yield conn
    conn.close()


def _insert_item(conn, item_id, status, first_seen_date):
    """Helper to insert a test item."""
    conn.execute(
        """
        INSERT INTO items (
            id, source, url, canonical_url, title, fetched_at,
            status, first_seen_date, last_seen_date, appearances
        ) VALUES (?, 'arxiv:x', 'https://a.com/1', 'https://a.com/1',
                  'Paper title', '2026-06-01T00:00:00Z',
                  ?, ?, ?, 1)
        """,
        (item_id, status, first_seen_date, first_seen_date),
    )
    conn.commit()


def _insert_run(conn, date):
    """Insert a runs row for the given date."""
    conn.execute(
        "INSERT INTO runs (date, items_fetched) VALUES (?, ?)",
        (date, 10),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# refetch() tests
# ---------------------------------------------------------------------------

class TestRefetch:
    def test_deletes_items_first_seen_today(self, db, tmp_path):
        _insert_item(db, "item-today-1", "new", "2026-07-01")
        _insert_item(db, "item-today-2", "candidate", "2026-07-01")
        _insert_item(db, "item-yesterday", "new", "2026-06-30")

        deleted = reset_mod.refetch(today="2026-07-01")

        assert deleted == 2
        remaining = db.execute("SELECT id FROM items").fetchall()
        assert len(remaining) == 1
        assert remaining[0]["id"] == "item-yesterday"

    def test_returns_zero_when_no_items_match(self, db, tmp_path):
        _insert_item(db, "item-old", "candidate", "2026-06-01")
        deleted = reset_mod.refetch(today="2026-07-01")
        assert deleted == 0

    def test_returns_zero_on_empty_db(self, db, tmp_path):
        deleted = reset_mod.refetch(today="2026-07-01")
        assert deleted == 0

    def test_uses_today_when_none_passed(self, db, tmp_path, monkeypatch):
        """When today is None, uses datetime.now()."""
        from datetime import datetime as real_datetime

        class FakeDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                class FakeDate:
                    def isoformat(self):
                        return "2026-07-01"
                return type("FakeResult", (), {"date": lambda self: FakeDate()})()

        monkeypatch.setattr(reset_mod, "datetime", FakeDatetime)
        _insert_item(db, "item-1", "new", "2026-07-01")
        deleted = reset_mod.refetch(today=None)
        assert deleted == 1


# ---------------------------------------------------------------------------
# force() tests
# ---------------------------------------------------------------------------

class TestForce:
    def test_resets_featured_to_candidate(self, db, tmp_path):
        _insert_item(db, "item-1", "featured", "2026-07-01")
        result = reset_mod.force(today="2026-07-01")
        row = db.execute("SELECT status, score, tags, why FROM items WHERE id='item-1'").fetchone()
        assert row["status"] == "candidate"
        assert row["score"] is None
        assert row["tags"] is None
        assert row["why"] is None
        assert result["items_reset"] == 1

    def test_resets_appendix_to_candidate(self, db, tmp_path):
        _insert_item(db, "item-1", "appendix", "2026-07-01")
        result = reset_mod.force(today="2026-07-01")
        row = db.execute("SELECT status FROM items WHERE id='item-1'").fetchone()
        assert row["status"] == "candidate"

    def test_resets_published_to_candidate(self, db, tmp_path):
        _insert_item(db, "item-1", "published", "2026-07-01")
        result = reset_mod.force(today="2026-07-01")
        row = db.execute("SELECT status FROM items WHERE id='item-1'").fetchone()
        assert row["status"] == "candidate"

    def test_does_not_reset_dropped(self, db, tmp_path):
        _insert_item(db, "item-1", "dropped", "2026-07-01")
        result = reset_mod.force(today="2026-07-01")
        row = db.execute("SELECT status FROM items WHERE id='item-1'").fetchone()
        assert row["status"] == "dropped"
        assert result["items_reset"] == 0

    def test_does_not_reset_new(self, db, tmp_path):
        _insert_item(db, "item-1", "new", "2026-07-01")
        result = reset_mod.force(today="2026-07-01")
        row = db.execute("SELECT status FROM items WHERE id='item-1'").fetchone()
        assert row["status"] == "new"
        assert result["items_reset"] == 0

    def test_does_not_reset_items_from_other_dates(self, db, tmp_path):
        _insert_item(db, "item-old", "featured", "2026-06-30")
        result = reset_mod.force(today="2026-07-01")
        row = db.execute("SELECT status FROM items WHERE id='item-old'").fetchone()
        assert row["status"] == "featured"
        assert result["items_reset"] == 0

    def test_deletes_runs_row(self, db, tmp_path):
        _insert_run(db, "2026-07-01")
        _insert_run(db, "2026-06-30")
        result = reset_mod.force(today="2026-07-01")
        assert result["runs_deleted"] == 1
        remaining = db.execute("SELECT date FROM runs").fetchall()
        assert len(remaining) == 1
        assert remaining[0]["date"] == "2026-06-30"

    def test_removes_issue_file(self, db, tmp_path):
        issues_dir = tmp_path / "site" / "src" / "content" / "issues"
        issues_dir.mkdir(parents=True)
        issue_file = issues_dir / "2026-07-01.md"
        issue_file.write_text("# test")
        result = reset_mod.force(today="2026-07-01")
        assert result["issue_removed"] is True
        assert not issue_file.exists()

    def test_issue_removed_false_when_no_file(self, db, tmp_path):
        result = reset_mod.force(today="2026-07-01")
        assert result["issue_removed"] is False

    def test_uses_today_when_none_passed(self, db, tmp_path, monkeypatch):
        from datetime import datetime as real_datetime

        class FakeDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                class FakeDate:
                    def isoformat(self):
                        return "2026-07-01"
                return type("FakeResult", (), {"date": lambda self: FakeDate()})()

        monkeypatch.setattr(reset_mod, "datetime", FakeDatetime)
        _insert_item(db, "item-1", "featured", "2026-07-01")
        result = reset_mod.force(today=None)
        assert result["items_reset"] == 1

    def test_resets_multiple_statuses_in_one_call(self, db, tmp_path):
        _insert_item(db, "item-f", "featured", "2026-07-01")
        _insert_item(db, "item-a", "appendix", "2026-07-01")
        _insert_item(db, "item-p", "published", "2026-07-01")
        result = reset_mod.force(today="2026-07-01")
        assert result["items_reset"] == 3
        for iid in ("item-f", "item-a", "item-p"):
            row = db.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()
            assert row["status"] == "candidate"


# ---------------------------------------------------------------------------
# main() CLI entry point tests
# ---------------------------------------------------------------------------

class TestMain:
    def test_refetch_command(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["reset.py", "refetch"])
        with patch.object(reset_mod, "refetch", return_value=1) as mock_refetch:
            rc = reset_mod.main()
        assert rc == 0
        mock_refetch.assert_called_once()

    def test_force_command(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["reset.py", "force"])
        with patch.object(reset_mod, "force", return_value={"items_reset": 0, "runs_deleted": 0, "issue_removed": False}) as mock_force:
            rc = reset_mod.main()
        assert rc == 0
        mock_force.assert_called_once()

    def test_invalid_command_returns_2(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["reset.py", "invalid"])
        rc = reset_mod.main()
        assert rc == 2
        captured = capsys.readouterr()
        assert "usage" in captured.err

    def test_no_args_returns_2(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["reset.py"])
        rc = reset_mod.main()
        assert rc == 2


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------

class TestConstants:
    def test_force_reset_statuses_are_valid(self):
        assert reset_mod.STATUS_FEATURED in reset_mod.FORCE_RESET_STATUSES
        assert reset_mod.STATUS_APPENDIX in reset_mod.FORCE_RESET_STATUSES
        assert reset_mod.STATUS_PUBLISHED in reset_mod.FORCE_RESET_STATUSES

    def test_dropped_not_in_force_reset(self):
        assert reset_mod.STATUS_DROPPED not in reset_mod.FORCE_RESET_STATUSES

    def test_new_not_in_force_reset(self):
        assert reset_mod.STATUS_NEW not in reset_mod.FORCE_RESET_STATUSES
