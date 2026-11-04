"""db.py helpers behind reset_day's forced re-run resets.

reset_day.py used to issue these statements inline. They now live in db.py so
the SQL has one home; these tests pin each helper's contract, and the guard at
the bottom keeps new inline SQL from creeping back into reset_day.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod
from models import MetaKey, Status

RESET_DAY_PY = Path(__file__).resolve().parents[1] / "scripts" / "reset_day.py"
DAY = "2026-10-22"
PREV = "2026-10-21"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _add(conn, item_id, status, *, first_seen=DAY, published_date=None):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "first_seen_date, last_seen_date, status, appearances, score, tags, why, "
        "published_date) "
        "VALUES (?, 's', ?, ?, 't', '2026-10-22T00:00:00Z', ?, ?, ?, 1, 9, 'x', 'w', ?)",
        (item_id, f"https://x/{item_id}", f"https://x/{item_id}",
         first_seen, DAY, status, published_date),
    )
    conn.commit()


def _row(conn, item_id):
    return conn.execute(
        "SELECT status, score, tags, why, published_date FROM items WHERE id = ?",
        (item_id,),
    ).fetchone()


def test_delete_meta_removes_only_that_key(conn):
    db_mod.set_meta(conn, MetaKey.FETCH_DONE, DAY)
    db_mod.set_meta(conn, "other", "v")
    db_mod.delete_meta(conn, MetaKey.FETCH_DONE)
    assert db_mod.get_meta(conn, MetaKey.FETCH_DONE) is None
    assert db_mod.get_meta(conn, "other") == "v"


def test_delete_meta_missing_key_is_noop(conn):
    db_mod.delete_meta(conn, "absent")
    assert db_mod.get_meta(conn, "absent") is None


def test_delete_items_first_seen_on(conn):
    _add(conn, "today", Status.NEW)
    _add(conn, "old", Status.NEW, first_seen=PREV)
    assert db_mod.delete_items_first_seen_on(conn, DAY) == 1
    ids = [r[0] for r in conn.execute("SELECT id FROM items")]
    assert ids == ["old"]
    assert db_mod.delete_items_first_seen_on(conn, DAY) == 0


def test_reset_processed_items_for_resets_processed_rows(conn):
    _add(conn, "f", Status.FEATURED)
    _add(conn, "a", Status.APPENDIX)
    _add(conn, "p", Status.PUBLISHED, published_date=DAY)
    assert db_mod.reset_processed_items_for(conn, DAY) == 3
    for item_id in ("f", "a", "p"):
        assert tuple(_row(conn, item_id)) == (Status.CANDIDATE, None, None, None, None)


def test_reset_processed_items_for_leaves_new_and_dropped(conn):
    _add(conn, "n", Status.NEW)
    _add(conn, "d", Status.DROPPED)
    assert db_mod.reset_processed_items_for(conn, DAY) == 0
    assert _row(conn, "n")["status"] == Status.NEW
    assert _row(conn, "d")["status"] == Status.DROPPED


def test_reset_processed_items_for_matches_published_date(conn):
    # Multi-day pool paper first seen earlier that won a slot today (#326).
    _add(conn, "pool", Status.PUBLISHED, first_seen=PREV, published_date=DAY)
    # Earlier day's published item re-surfaced today: untouched (#321).
    _add(conn, "old", Status.PUBLISHED, first_seen=PREV, published_date=PREV)
    assert db_mod.reset_processed_items_for(conn, DAY) == 1
    assert _row(conn, "pool")["status"] == Status.CANDIDATE
    assert tuple(_row(conn, "old")) == (Status.PUBLISHED, 9, "x", "w", PREV)


def test_delete_run(conn):
    conn.execute("INSERT INTO runs (date) VALUES (?)", (DAY,))
    conn.execute("INSERT INTO runs (date) VALUES (?)", (PREV,))
    conn.commit()
    assert db_mod.delete_run(conn, DAY) == 1
    assert [r[0] for r in conn.execute("SELECT date FROM runs")] == [PREV]
    assert db_mod.delete_run(conn, DAY) == 0


def test_reset_day_has_no_inline_execute():
    """reset_day.py's state changes go through db.py helpers."""
    tree = ast.parse(RESET_DAY_PY.read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls == [], f"move new reset_day SQL into db.py helpers: lines {calls}"
