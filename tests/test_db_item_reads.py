"""db.py helpers behind the candidate-pool read and item lookup by id.

candidates.py and replay_writer.py used to issue these SELECTs inline. They
now live in db.py so the SQL has one home; these tests pin each helper's
contract, and the guards at the bottom keep new inline SQL from creeping back
into either module.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod
from models import Section, Status

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


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


def test_load_candidate_rows_only_candidates(conn):
    _add(conn, "c1", Status.CANDIDATE, section=Section.PAPERS, score=7)
    _add(conn, "c2", Status.CANDIDATE)
    for other in (Status.NEW, Status.FEATURED, Status.APPENDIX, Status.DROPPED):
        _add(conn, f"x-{other}", other)
    rows = db_mod.load_candidate_rows(conn)
    assert sorted(r["id"] for r in rows) == ["c1", "c2"]
    assert set(rows[0].keys()) == {
        "id", "source", "url", "title", "author", "published_at", "raw_text",
        "section", "section_override", "score", "tags", "why",
    }


def test_load_candidate_rows_empty_db(conn):
    assert db_mod.load_candidate_rows(conn) == []


def test_load_item_by_id_any_status(conn):
    _add(conn, "f", Status.FEATURED, section=Section.PAPERS, score=9)
    _add(conn, "d", Status.DROPPED)
    row = db_mod.load_item_by_id(conn, "f")
    assert row["id"] == "f"
    assert row["section"] == Section.PAPERS
    assert row["score"] == 9
    assert set(row.keys()) == {
        "id", "source", "url", "canonical_url", "title", "author",
        "published_at", "raw_text", "score", "tags", "why", "section",
    }
    assert db_mod.load_item_by_id(conn, "d")["id"] == "d"


def test_load_item_by_id_missing(conn):
    assert db_mod.load_item_by_id(conn, "nope") is None


@pytest.mark.parametrize("module", ["candidates.py", "replay_writer.py"])
def test_module_has_no_inline_execute(module):
    """Item reads in these modules go through db.py helpers."""
    tree = ast.parse((SCRIPTS / module).read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls == [], f"move new {module} SQL into db.py helpers: lines {calls}"
