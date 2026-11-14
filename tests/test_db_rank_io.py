"""db.py helpers behind rank's item-state writes.

rank.py used to issue these UPDATEs inline. They now live in db.py so the
stage reads as ranking logic and the SQL has one home; these tests pin each
helper's contract, and the guard at the bottom keeps new inline SQL from
creeping back into rank.py.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import db as db_mod
from models import Section, Status

RANK_PY = Path(__file__).resolve().parents[1] / "scripts" / "rank.py"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _add(conn, item_id, status, *, section=Section.PAPERS, times_competed=0):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "first_seen_date, last_seen_date, status, section, appearances, times_competed) "
        "VALUES (?, 's', ?, ?, 't', '2026-06-01T00:00:00Z', '2026-06-01', '2026-06-01', "
        "?, ?, 1, ?)",
        (item_id, f"https://x/{item_id}", f"https://x/{item_id}",
         status, section, times_competed),
    )
    conn.commit()


def _row(conn, item_id):
    return dict(conn.execute(
        "SELECT status, score, tags, why, times_competed FROM items WHERE id = ?",
        (item_id,),
    ).fetchone())


def test_apply_rank_decisions_writes_all_fields(conn):
    _add(conn, "a", Status.CANDIDATE)
    _add(conn, "b", Status.CANDIDATE)
    db_mod.apply_rank_decisions(conn, [
        ("a", 9, json.dumps(["agents"]), "great", Status.FEATURED),
        ("b", 3, "[]", "meh", Status.DROPPED),
    ])
    assert _row(conn, "a") == {"status": Status.FEATURED, "score": 9,
                               "tags": '["agents"]', "why": "great", "times_competed": 0}
    assert _row(conn, "b")["status"] == Status.DROPPED


def test_apply_rank_decisions_commits(conn, tmp_path):
    _add(conn, "a", Status.CANDIDATE)
    db_mod.apply_rank_decisions(conn, [("a", 7, "[]", "ok", Status.APPENDIX)])
    other = db_mod.connect(tmp_path / "state.db")
    try:
        assert other.execute("SELECT status FROM items WHERE id='a'").fetchone()[0] == Status.APPENDIX
    finally:
        other.close()


def test_apply_rank_decisions_empty_is_noop(conn):
    _add(conn, "a", Status.CANDIDATE)
    before = _row(conn, "a")
    assert db_mod.apply_rank_decisions(conn, []) is None
    assert _row(conn, "a") == before
    assert before["status"] == Status.CANDIDATE


def test_bump_times_competed_only_touches_candidates(conn):
    _add(conn, "pool", Status.CANDIDATE, times_competed=1)
    _add(conn, "won", Status.FEATURED)
    _add(conn, "other", Status.CANDIDATE)
    assert db_mod.bump_times_competed(conn, ["pool", "won"], "2026-06-01") == 1
    assert _row(conn, "pool")["times_competed"] == 2
    assert _row(conn, "won")["times_competed"] == 0
    assert _row(conn, "other")["times_competed"] == 0


def test_bump_times_competed_empty_is_noop(conn):
    assert db_mod.bump_times_competed(conn, [], "2026-06-01") == 0


def test_rank_has_no_inline_execute():
    """rank.py's item-state SQL goes through db.py helpers."""
    tree = ast.parse(RANK_PY.read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls == [], f"move new rank SQL into db.py helpers: lines {calls}"
