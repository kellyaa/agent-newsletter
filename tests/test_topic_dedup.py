"""Tests for topic-level cross-day dedup (issue #4).

Covers:
  - items.topic column exists after init/migration.
  - rank.load_recent_topics returns distinct slugs from the last N days.
  - rank.build_prompt injects the "Topics covered in the last 7 days" block.
  - rank.persist stores topic slugs on each item.
  - publish INSERTs one topics_covered row per promoted item that has a
    non-empty topic, is idempotent, and skips empty-topic items.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest


SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    import importlib
    import db
    importlib.reload(db)
    p = tmp_path / "state.db"
    db.init_db(p)
    return p


def test_items_topic_column_present_after_init(db_path):
    import db
    conn = db.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    conn.close()
    assert "topic" in cols


def test_migration_adds_topic_column_to_legacy_db(tmp_path, monkeypatch):
    """A DB missing the `topic` column gets it via _migrate()."""
    import sqlite3
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE items (
          id TEXT PRIMARY KEY, source TEXT NOT NULL, url TEXT NOT NULL,
          canonical_url TEXT NOT NULL, title TEXT NOT NULL, author TEXT,
          published_at TEXT, fetched_at TEXT NOT NULL, raw_text TEXT,
          score INTEGER, tags TEXT, section TEXT, section_override TEXT,
          why TEXT, status TEXT NOT NULL, first_seen_date TEXT NOT NULL,
          last_seen_date TEXT NOT NULL, appearances INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    import importlib
    import db
    importlib.reload(db)
    db.init_db(p)

    conn = db.connect(p)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    conn.close()
    assert "topic" in cols
    assert "times_competed" in cols


def test_load_recent_topics_returns_distinct_recent(db_path):
    import db
    conn = db.connect(db_path)
    conn.execute("INSERT INTO topics_covered (topic, date, item_id) VALUES (?, ?, ?)",
                 ("anthropic-sdk", "2026-07-10", "a"))
    conn.execute("INSERT INTO topics_covered (topic, date, item_id) VALUES (?, ?, ?)",
                 ("anthropic-sdk", "2026-07-11", "b"))
    conn.execute("INSERT INTO topics_covered (topic, date, item_id) VALUES (?, ?, ?)",
                 ("langgraph-checkpointing", "2026-07-12", "c"))
    conn.commit()

    # Freeze SQLite's date('now') via a large days-back window (7000 days).
    import rank
    got = rank.load_recent_topics(conn, days=7000)
    topics = {r["topic"] for r in got}
    conn.close()
    assert topics == {"anthropic-sdk", "langgraph-checkpointing"}


def test_load_recent_topics_ignores_empty(db_path):
    import db, rank
    conn = db.connect(db_path)
    conn.execute("INSERT INTO topics_covered (topic, date, item_id) VALUES (?, ?, ?)",
                 ("", "2026-07-10", "a"))
    conn.commit()
    got = rank.load_recent_topics(conn, days=7000)
    conn.close()
    assert got == []


def test_build_prompt_injects_recent_topics_block(db_path):
    import rank
    items = [{"id": "x", "title": "t", "url": "u", "source": "s"}]
    prompt = rank.build_prompt("news", items, "RUBRIC",
                               [{"topic": "anthropic-sdk", "date": "2026-07-10"}])
    assert "Topics covered in the last 7 days" in prompt
    assert "anthropic-sdk" in prompt
    assert "RUBRIC" in prompt


def test_build_prompt_omits_block_when_no_topics(db_path):
    import rank
    items = [{"id": "x", "title": "t", "url": "u", "source": "s"}]
    prompt = rank.build_prompt("news", items, "RUBRIC", [])
    assert "Topics covered" not in prompt
    prompt = rank.build_prompt("news", items, "RUBRIC", None)
    assert "Topics covered" not in prompt


def _insert_item(conn, item_id: str, status: str, section: str = "news",
                 topic: str | None = None):
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, fetched_at, status,
            section, first_seen_date, last_seen_date, appearances, topic
        ) VALUES (?, 'rss:t', 'https://ex.com/x', 'https://ex.com/x',
                  'T', '2026-06-01T00:00:00Z', ?, ?, '2026-06-01',
                  '2026-06-01', 1, ?)
        """,
        (item_id, status, section, topic),
    )
    conn.commit()


def test_persist_writes_topic_column(db_path):
    import db, rank
    conn = db.connect(db_path)
    _insert_item(conn, "n1", "candidate", "news")
    rank.persist(conn, {
        "n1": {"status": "featured", "score": 8, "tags": ["evals"],
               "why": "solid", "section": "news", "topic": "eval-mistakes"}
    })
    row = conn.execute("SELECT topic FROM items WHERE id='n1'").fetchone()
    conn.close()
    assert row["topic"] == "eval-mistakes"


def test_persist_empty_topic_stored_as_null(db_path):
    import db, rank
    conn = db.connect(db_path)
    _insert_item(conn, "n1", "candidate", "news")
    rank.persist(conn, {
        "n1": {"status": "featured", "score": 8, "tags": [],
               "why": "x", "section": "news", "topic": ""}
    })
    row = conn.execute("SELECT topic FROM items WHERE id='n1'").fetchone()
    conn.close()
    assert row["topic"] is None


def test_publish_writes_topics_covered(db_path, tmp_path, monkeypatch):
    """publish.py copies non-empty item.topic values into topics_covered."""
    import db
    conn = db.connect(db_path)
    _insert_item(conn, "n1", "featured", "news", topic="anthropic-sdk")
    _insert_item(conn, "n2", "appendix", "news", topic="langgraph-checkpointing")
    _insert_item(conn, "n3", "featured", "news", topic=None)  # skipped
    conn.close()

    # Fake issue file so publish.main() doesn't bail on missing file.
    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    import importlib
    import db as db_mod; importlib.reload(db_mod)
    import publish; importlib.reload(publish)

    issues_dir = tmp_path / "site" / "src" / "content" / "issues"
    issues_dir.mkdir(parents=True)
    today = datetime.now().date().isoformat()
    (issues_dir / f"{today}.md").write_text("---\ndate: x\n---\n" + "x" * 500)

    rc = publish.main()
    assert rc == 0

    conn = db_mod.connect(db_path)
    rows = conn.execute("SELECT topic, item_id FROM topics_covered ORDER BY topic").fetchall()
    conn.close()
    topics = {(r["topic"], r["item_id"]) for r in rows}
    assert ("anthropic-sdk", "n1") in topics
    assert ("langgraph-checkpointing", "n2") in topics
    assert len(topics) == 2  # n3 (topic=NULL) was correctly skipped


def test_publish_topics_covered_is_idempotent(db_path, tmp_path, monkeypatch):
    """A second publish for the same day is a no-op (INSERT OR IGNORE)."""
    import db
    conn = db.connect(db_path)
    _insert_item(conn, "n1", "featured", "news", topic="anthropic-sdk")
    conn.close()

    monkeypatch.setenv("CONTENT_ROOT", str(tmp_path))
    import importlib
    import db as db_mod; importlib.reload(db_mod)
    import publish; importlib.reload(publish)

    issues_dir = tmp_path / "site" / "src" / "content" / "issues"
    issues_dir.mkdir(parents=True)
    today = datetime.now().date().isoformat()
    (issues_dir / f"{today}.md").write_text("---\ndate: x\n---\n" + "x" * 500)

    assert publish.main() == 0
    # Second publish (idempotent skip returns 0 without re-inserting).
    assert publish.main() == 0

    conn = db_mod.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM topics_covered").fetchone()[0]
    conn.close()
    assert count == 1
