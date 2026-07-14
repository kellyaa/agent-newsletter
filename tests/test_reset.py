"""Tests for scripts/reset.py — pipeline state reset helpers."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db import init_db, connect
from reset import force_reset, refetch, today_iso


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Create a temporary state.db and patch DB_PATH."""
    import db as db_mod
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(db_mod, "CONTENT_ROOT", tmp_path)
    init_db(db_path)
    return db_path


def _seed_items(db_path: Path, today: str):
    """Insert test rows in various statuses for today."""
    conn = sqlite3.connect(db_path)
    for i, status in enumerate(["featured", "appendix", "published", "candidate", "dropped"]):
        conn.execute(
            """INSERT INTO items (id, source, url, canonical_url, title, fetched_at,
               status, first_seen_date, last_seen_date, keyword_gate_bypass)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                f"id-{i}", f"rss:test", f"http://example.com/{i}",
                f"http://example.com/{i}", f"Title {i}",
                f"{today}T00:00:00+00:00", status, today, today,
            ),
        )
    conn.execute(
        "INSERT INTO runs (date, items_fetched) VALUES (?, ?)",
        (today, 5),
    )
    conn.commit()
    conn.close()


def test_force_reset_resets_sealed_items(tmp_db, tmp_path, monkeypatch):
    """--force resets featured/appendix/published to candidate, clears scores."""
    import reset as reset_mod
    monkeypatch.setattr(reset_mod, "ISSUES_DIR", tmp_path / "issues")
    today = today_iso()
    _seed_items(tmp_db, today)

    # Create a fake issue file
    issues_dir = tmp_path / "issues"
    issues_dir.mkdir()
    issue_file = issues_dir / f"{today}.md"
    issue_file.write_text("---\ntest\n---\n")

    force_reset(today)

    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, status, score FROM items").fetchall()
    statuses = {r["id"]: r["status"] for r in rows}

    # featured, appendix, published → candidate
    assert statuses["id-0"] == "candidate"
    assert statuses["id-1"] == "candidate"
    assert statuses["id-2"] == "candidate"
    # candidate stays candidate (not touched — already in right state)
    assert statuses["id-3"] == "candidate"
    # dropped stays dropped
    assert statuses["id-4"] == "dropped"

    # scores cleared for reset items
    for r in rows:
        if r["id"] in ("id-0", "id-1", "id-2"):
            assert r["score"] is None

    # runs row deleted
    runs = conn.execute("SELECT * FROM runs WHERE date = ?", (today,)).fetchone()
    assert runs is None

    # issue file deleted
    assert not issue_file.exists()
    conn.close()


def test_refetch_deletes_today_items(tmp_db):
    """--refetch deletes all items first seen today."""
    today = today_iso()
    _seed_items(tmp_db, today)

    deleted = refetch(today)
    assert deleted == 5

    conn = sqlite3.connect(tmp_db)
    remaining = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert remaining == 0
    conn.close()


def test_today_iso_returns_utc_date():
    """today_iso() returns a date string in YYYY-MM-DD format."""
    result = today_iso()
    assert len(result) == 10
    assert result[4] == "-" and result[7] == "-"
