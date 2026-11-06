"""db.py helpers behind write's issue-assembly reads.

write.py used to issue these SELECTs inline. They now live in db.py so the
stage reads as issue assembly and the SQL has one home; these tests pin each
helper's contract, and the guard at the bottom keeps new inline SQL from
creeping back into write.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod
from models import Section, Status

WRITE_PY = Path(__file__).resolve().parents[1] / "scripts" / "write.py"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _add(conn, item_id, status, *, section=Section.BLOGS, score=None):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "first_seen_date, last_seen_date, status, section, appearances, score) "
        "VALUES (?, 's', ?, ?, 't', '2026-06-01T00:00:00Z', '2026-06-01', '2026-06-01', "
        "?, ?, 1, ?)",
        (item_id, f"https://x/{item_id}", f"https://x/{item_id}",
         status, section, score),
    )
    conn.commit()


def test_load_featured_items_filters_and_orders(conn):
    _add(conn, "b1", Status.FEATURED, section=Section.BLOGS, score=5)
    _add(conn, "b2", Status.FEATURED, section=Section.BLOGS, score=9)
    _add(conn, "p1", Status.FEATURED, section=Section.PAPERS, score=7)
    _add(conn, "x", Status.APPENDIX, section=Section.BLOGS, score=10)
    _add(conn, "c", Status.CANDIDATE, section=Section.BLOGS, score=10)
    rows = db_mod.load_featured_items(conn)
    assert [r["id"] for r in rows] == ["b2", "b1", "p1"]
    assert set(rows[0].keys()) == {
        "id", "source", "url", "canonical_url", "title", "author",
        "published_at", "raw_text", "score", "tags", "why", "section",
    }


def test_load_featured_items_ties_break_on_id(conn):
    _add(conn, "z", Status.FEATURED, score=5)
    _add(conn, "a", Status.FEATURED, score=5)
    assert [r["id"] for r in db_mod.load_featured_items(conn)] == ["a", "z"]


def test_load_appendix_items_filters_and_orders(conn):
    _add(conn, "p2", Status.APPENDIX, section=Section.PAPERS)
    _add(conn, "b2", Status.APPENDIX, section=Section.BLOGS)
    _add(conn, "b1", Status.APPENDIX, section=Section.BLOGS)
    _add(conn, "f", Status.FEATURED, section=Section.BLOGS, score=9)
    rows = db_mod.load_appendix_items(conn)
    assert [r["id"] for r in rows] == ["b1", "b2", "p2"]
    assert set(rows[0].keys()) == {
        "id", "source", "url", "canonical_url", "title", "section",
    }


def test_loaders_empty_db(conn):
    assert db_mod.load_featured_items(conn) == []
    assert db_mod.load_appendix_items(conn) == []


def test_write_has_no_inline_execute():
    """write.py's item reads go through db.py helpers."""
    tree = ast.parse(WRITE_PY.read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls == [], f"move new write SQL into db.py helpers: lines {calls}"


def test_demote_featured_to_appendix_only_moves_featured(conn):
    _add(conn, "f1", Status.FEATURED, score=9)
    _add(conn, "f2", Status.FEATURED, score=9)
    _add(conn, "p", Status.PUBLISHED, score=9)
    assert db_mod.demote_featured_to_appendix(conn, ["f1", "p"]) == 1
    assert db_mod.demote_featured_to_appendix(conn, []) == 0
    got = {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM items")}
    assert got == {"f1": "appendix", "f2": "featured", "p": "published"}


def test_reset_unpublished_featured_clears_scores(conn):
    _add(conn, "f1", Status.FEATURED, score=9)
    _add(conn, "a", Status.APPENDIX, score=5)
    _add(conn, "p", Status.PUBLISHED, score=9)
    assert db_mod.reset_unpublished_featured(conn) == 1
    rows = {r["id"]: (r["status"], r["score"]) for r in conn.execute("SELECT id, status, score FROM items")}
    assert rows == {"f1": ("candidate", None), "a": ("appendix", 5), "p": ("published", 9)}
