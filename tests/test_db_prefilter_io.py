"""db.py helpers behind prefilter's item-state transitions.

prefilter.py used to issue these statements inline. They now live in db.py so
the stage reads as gating logic and the SQL has one home; these tests pin
each helper's contract, and the guard at the bottom keeps new inline SQL from
creeping back into prefilter.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod
from models import Section, Status

PREFILTER_PY = Path(__file__).resolve().parents[1] / "scripts" / "prefilter.py"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _add(conn, item_id, status, *, section=None, score=None, appearances=0):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "first_seen_date, last_seen_date, status, section, score, appearances) "
        "VALUES (?, 's', ?, ?, 't', '2026-06-01T00:00:00Z', '2026-06-01', '2026-06-01', "
        "?, ?, ?, ?)",
        (item_id, f"https://x/{item_id}", f"https://x/{item_id}",
         status, section, score, appearances),
    )
    conn.commit()


def _row(conn, item_id):
    return dict(conn.execute(
        "SELECT status, section, score FROM items WHERE id = ?", (item_id,)
    ).fetchone())


def test_load_prefilter_eligible_takes_new_and_retryable_appendix(conn):
    _add(conn, "new", Status.NEW)
    _add(conn, "app-retry", Status.APPENDIX, appearances=1)
    _add(conn, "app-spent", Status.APPENDIX, appearances=2)
    _add(conn, "feat", Status.FEATURED)
    rows = db_mod.load_prefilter_eligible(conn, 2)
    assert sorted(r["id"] for r in rows) == ["app-retry", "new"]
    assert {"keyword_gate_bypass", "recency_days_override", "section_override"} <= set(rows[0])


def test_promote_to_candidates_sets_status_and_section(conn):
    _add(conn, "a", Status.NEW)
    _add(conn, "b", Status.APPENDIX)
    db_mod.promote_to_candidates(conn, [("a", Section.PAPERS), ("b", Section.NEWS)])
    assert _row(conn, "a")["status"] == Status.CANDIDATE
    assert _row(conn, "a")["section"] == Section.PAPERS
    assert _row(conn, "b")["section"] == Section.NEWS


def test_promote_to_candidates_empty_is_noop(conn):
    db_mod.promote_to_candidates(conn, [])


def test_drop_items_moves_only_given_ids(conn):
    _add(conn, "a", Status.NEW)
    _add(conn, "b", Status.NEW)
    assert db_mod.drop_items(conn, ["a"]) == 1
    assert _row(conn, "a")["status"] == Status.DROPPED
    assert _row(conn, "b")["status"] == Status.NEW
    assert db_mod.drop_items(conn, []) == 0


def test_clear_candidate_scores_scoped_to_section_and_status(conn):
    _add(conn, "p", Status.CANDIDATE, section=Section.PAPERS, score=7)
    _add(conn, "n", Status.CANDIDATE, section=Section.NEWS, score=7)
    _add(conn, "f", Status.FEATURED, section=Section.PAPERS, score=7)
    _add(conn, "u", Status.CANDIDATE, section=Section.PAPERS)
    assert db_mod.clear_candidate_scores(conn, Section.PAPERS) == 1
    assert _row(conn, "p")["score"] is None
    assert _row(conn, "n")["score"] == 7
    assert _row(conn, "f")["score"] == 7


def test_count_never_scored_expired(conn):
    _add(conn, "d", Status.DROPPED, section=Section.PAPERS)
    _add(conn, "e", Status.EXPIRED_UNJUDGED, section=Section.PAPERS)
    _add(conn, "scored", Status.DROPPED, section=Section.PAPERS, score=3)
    _add(conn, "news", Status.DROPPED, section=Section.NEWS)
    _add(conn, "live", Status.CANDIDATE, section=Section.PAPERS)
    assert db_mod.count_never_scored_expired(conn, Section.PAPERS) == 2


def test_prefilter_has_one_inline_execute_only():
    """Only the shared PAPER_AGE_OUT_SQL (also used by backfill) runs inline."""
    tree = ast.parse(PREFILTER_PY.read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert len(calls) == 1, f"move new prefilter SQL into db.py helpers: lines {calls}"
