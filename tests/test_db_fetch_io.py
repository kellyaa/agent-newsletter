"""db.upsert_item: the items upsert behind fetch.upsert_items.

fetch.py used to issue this INSERT ... ON CONFLICT inline. It now lives in
db.py so the SQL has one home; these tests pin the helper's contract, and the
guard at the bottom keeps new inline SQL from creeping back into fetch.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod
from models import Status

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _upsert(conn, **over):
    kw = dict(
        item_id="a", source="rss:x", url="https://x/a", canonical_url="https://x/a",
        title="T", author="me", published_at="2026-06-01T00:00:00Z",
        fetched_at="2026-06-01T01:00:00Z", raw_text="body", section_override=None,
        keyword_gate_bypass=False, recency_days_override=None, seen_date="2026-06-01",
    )
    kw.update(over)
    return db_mod.upsert_item(conn, **kw)


def _row(conn, item_id="a"):
    return dict(conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone())


def test_insert_new_row(conn):
    assert _upsert(conn) is True
    r = _row(conn)
    assert r["status"] == Status.NEW
    assert (r["source"], r["url"], r["canonical_url"], r["title"], r["author"]) == (
        "rss:x", "https://x/a", "https://x/a", "T", "me")
    assert (r["published_at"], r["fetched_at"], r["raw_text"]) == (
        "2026-06-01T00:00:00Z", "2026-06-01T01:00:00Z", "body")
    assert (r["first_seen_date"], r["last_seen_date"], r["appearances"]) == (
        "2026-06-01", "2026-06-01", 1)
    assert (r["section_override"], r["keyword_gate_bypass"], r["recency_days_override"]) == (
        None, 0, None)


def test_insert_does_not_commit(conn):
    _upsert(conn)
    assert conn.in_transaction
    conn.commit()


def test_reseen_bumps_appearances_and_last_seen_only(conn):
    _upsert(conn)
    conn.execute("UPDATE items SET status = ? WHERE id = 'a'", (Status.CANDIDATE,))
    assert _upsert(conn, title="New title", fetched_at="2026-06-02T01:00:00Z",
                   seen_date="2026-06-02") is True
    r = _row(conn)
    assert (r["appearances"], r["first_seen_date"], r["last_seen_date"]) == (
        2, "2026-06-01", "2026-06-02")
    # Untouched on conflict.
    assert (r["title"], r["fetched_at"], r["status"]) == (
        "T", "2026-06-01T01:00:00Z", Status.CANDIDATE)


def test_reseen_merges_overrides(conn):
    _upsert(conn, section_override="papers", keyword_gate_bypass=True,
            recency_days_override=7)
    # NULL / False on a later sighting keeps the stored overrides.
    _upsert(conn, seen_date="2026-06-02")
    r = _row(conn)
    assert (r["section_override"], r["keyword_gate_bypass"], r["recency_days_override"]) == (
        "papers", 1, 7)
    # A set override replaces the stored one.
    _upsert(conn, section_override="news", recency_days_override=3, seen_date="2026-06-03")
    r = _row(conn)
    assert (r["section_override"], r["keyword_gate_bypass"], r["recency_days_override"]) == (
        "news", 1, 3)


def test_bypass_turns_on_for_existing_row(conn):
    _upsert(conn)
    _upsert(conn, keyword_gate_bypass=True, seen_date="2026-06-02")
    assert _row(conn)["keyword_gate_bypass"] == 1


def test_fetch_has_no_inline_execute():
    """SQL in fetch.py goes through db.py helpers."""
    tree = ast.parse((SCRIPTS / "fetch.py").read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls == [], f"move new fetch.py SQL into db.py helpers: lines {calls}"
