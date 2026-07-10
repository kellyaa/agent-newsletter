"""Tests for publish.py: record_run(), main() exit paths, and idempotency."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """Initialise a fresh in-memory-like SQLite DB in tmp_path and return the
    connection. The DB has the full agent-newsletter schema."""
    import db as db_mod
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def db_path(tmp_path):
    """Return a path to a freshly initialised DB (for callers that need the path)."""
    import db as db_mod
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p


def _insert_item(conn, item_id: str, status: str, section: str = "papers") -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, fetched_at, status,
            section, first_seen_date, last_seen_date, appearances
        ) VALUES (?, 'rss:test', 'https://example.com/1', 'https://example.com/1',
                  'Test Title', '2026-06-01T00:00:00Z', ?, ?,
                  '2026-06-01', '2026-06-01', 1)
        """,
        (item_id, status, section),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# record_run()
# ---------------------------------------------------------------------------

class TestRecordRun:
    def test_inserts_row_correctly(self, db):
        from publish import record_run

        record_run(db, "2026-06-01", {"papers": 3, "news": 2, "blogs": 1}, 4, 100)
        row = db.execute("SELECT * FROM runs WHERE date = '2026-06-01'").fetchone()
        assert row is not None
        # items_featured = sum(featured_counts.values()) = 3+2+1 = 6
        assert row["items_featured"] == 6
        # items_candidate = papers+news+blogs+appendix_count = 6+4 = 10
        assert row["items_candidate"] == 10
        assert row["items_papers"] == 3
        assert row["items_news"] == 2
        assert row["items_blogs"] == 1
        assert row["items_fetched"] == 100
        assert "appendix=4" in (row["notes"] or "")

    def test_upsert_updates_existing_row(self, db):
        from publish import record_run

        record_run(db, "2026-06-01", {"papers": 1}, 0, 50)
        record_run(db, "2026-06-01", {"papers": 3}, 2, 80)
        row = db.execute("SELECT * FROM runs WHERE date = '2026-06-01'").fetchone()
        assert row["items_papers"] == 3
        assert row["items_fetched"] == 80

    def test_zero_featured_counts(self, db):
        from publish import record_run

        record_run(db, "2026-06-15", {}, 0, 0)
        row = db.execute("SELECT * FROM runs WHERE date = '2026-06-15'").fetchone()
        assert row is not None
        assert row["items_featured"] == 0

    def test_appendix_count_included_in_total(self, db):
        from publish import record_run

        record_run(db, "2026-06-20", {"blogs": 2}, 5, 30)
        row = db.execute("SELECT * FROM runs WHERE date = '2026-06-20'").fetchone()
        # items_candidate = sum(featured_counts) + appendix_count = 2 + 5 = 7
        assert row["items_candidate"] == 7
        # items_featured = sum(featured_counts.values()) only = 2
        assert row["items_featured"] == 2


# ---------------------------------------------------------------------------
# main() — missing issue file
# ---------------------------------------------------------------------------

class TestMainMissingFile:
    def test_returns_2_when_issue_file_absent(self, db_path, tmp_path, monkeypatch):
        from publish import main as publish_main
        import publish as publish_mod

        # Point ISSUES_DIR to an empty directory
        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        monkeypatch.setattr(publish_mod, "ISSUES_DIR", issues_dir)

        # Patch connect/init_db to use our test DB
        import db as db_mod
        monkeypatch.setattr(publish_mod, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(publish_mod, "init_db", lambda: db_mod.init_db(db_path))

        with patch("publish.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-06-01"
            result = publish_main()

        assert result == 2


# ---------------------------------------------------------------------------
# main() — issue file too small
# ---------------------------------------------------------------------------

class TestMainFileTooSmall:
    def test_returns_4_when_file_below_min_size(self, db_path, tmp_path, monkeypatch):
        from publish import main as publish_main
        import publish as publish_mod

        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        small_file = issues_dir / "2026-06-01.md"
        small_file.write_text("tiny")  # << MIN_FILE_SIZE_BYTES
        monkeypatch.setattr(publish_mod, "ISSUES_DIR", issues_dir)

        import db as db_mod
        monkeypatch.setattr(publish_mod, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(publish_mod, "init_db", lambda: db_mod.init_db(db_path))

        with patch("publish.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-06-01"
            result = publish_main()

        assert result == 4


# ---------------------------------------------------------------------------
# main() — empty issue (no featured, no appendix)
# ---------------------------------------------------------------------------

class TestMainEmptyIssue:
    def test_returns_5_when_no_featured_no_appendix(self, db_path, tmp_path, monkeypatch):
        from publish import main as publish_main, MIN_FILE_SIZE_BYTES
        import publish as publish_mod

        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        issue_file = issues_dir / "2026-06-01.md"
        # Write a file that passes the size check but has no DB items
        issue_file.write_text("x" * (MIN_FILE_SIZE_BYTES + 10))
        monkeypatch.setattr(publish_mod, "ISSUES_DIR", issues_dir)

        import db as db_mod
        monkeypatch.setattr(publish_mod, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(publish_mod, "init_db", lambda: db_mod.init_db(db_path))

        with patch("publish.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-06-01"
            result = publish_main()

        assert result == 5


# ---------------------------------------------------------------------------
# main() — normal publish path
# ---------------------------------------------------------------------------

class TestMainNormalPublish:
    def test_promotes_featured_and_appendix_to_published(self, db_path, tmp_path, monkeypatch):
        from publish import main as publish_main, MIN_FILE_SIZE_BYTES
        import publish as publish_mod
        import db as db_mod

        # Insert DB items
        conn = db_mod.connect(db_path)
        _insert_item(conn, "feat-1", "featured", "papers")
        _insert_item(conn, "feat-2", "featured", "news")
        _insert_item(conn, "app-1", "appendix", "blogs")
        conn.close()

        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        issue_file = issues_dir / "2026-06-01.md"
        issue_file.write_text("x" * (MIN_FILE_SIZE_BYTES + 50))
        monkeypatch.setattr(publish_mod, "ISSUES_DIR", issues_dir)

        def _make_conn():
            return db_mod.connect(db_path)

        monkeypatch.setattr(publish_mod, "connect", _make_conn)
        monkeypatch.setattr(publish_mod, "init_db", lambda: db_mod.init_db(db_path))

        with patch("publish.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-06-01"
            result = publish_main()

        assert result == 0

        conn = db_mod.connect(db_path)
        statuses = {
            r["id"]: r["status"]
            for r in conn.execute("SELECT id, status FROM items").fetchall()
        }
        conn.close()
        assert statuses["feat-1"] == "published"
        assert statuses["feat-2"] == "published"
        assert statuses["app-1"] == "published"

    def test_writes_runs_row_on_normal_publish(self, db_path, tmp_path, monkeypatch):
        from publish import main as publish_main, MIN_FILE_SIZE_BYTES
        import publish as publish_mod
        import db as db_mod

        conn = db_mod.connect(db_path)
        _insert_item(conn, "feat-1", "featured", "papers")
        conn.close()

        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        (issues_dir / "2026-06-01.md").write_text("x" * (MIN_FILE_SIZE_BYTES + 50))
        monkeypatch.setattr(publish_mod, "ISSUES_DIR", issues_dir)

        monkeypatch.setattr(publish_mod, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(publish_mod, "init_db", lambda: db_mod.init_db(db_path))

        with patch("publish.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-06-01"
            publish_main()

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT * FROM runs WHERE date = '2026-06-01'").fetchone()
        conn.close()
        assert row is not None
        assert row["items_papers"] == 1


# ---------------------------------------------------------------------------
# main() — idempotent skip
# ---------------------------------------------------------------------------

class TestMainIdempotentSkip:
    def test_skip_when_already_published(self, db_path, tmp_path, monkeypatch):
        """If a runs row exists and no featured/appendix items remain, skip."""
        from publish import main as publish_main, MIN_FILE_SIZE_BYTES, record_run
        import publish as publish_mod
        import db as db_mod

        # Pre-populate the runs row (simulating a prior completed publish)
        conn = db_mod.connect(db_path)
        record_run(conn, "2026-06-01", {"papers": 1}, 0, 10)
        conn.close()

        issues_dir = tmp_path / "issues"
        issues_dir.mkdir()
        (issues_dir / "2026-06-01.md").write_text("x" * (MIN_FILE_SIZE_BYTES + 50))
        monkeypatch.setattr(publish_mod, "ISSUES_DIR", issues_dir)

        monkeypatch.setattr(publish_mod, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(publish_mod, "init_db", lambda: db_mod.init_db(db_path))

        with patch("publish.datetime") as mock_dt:
            mock_dt.now.return_value.date.return_value.isoformat.return_value = "2026-06-01"
            result = publish_main()

        assert result == 0
