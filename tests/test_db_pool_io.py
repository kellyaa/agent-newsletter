"""db.py helpers behind the papers-pool age-out and backfill's live promotion.

prefilter.py and backfill.py used to run these UPDATEs inline. They now live
in db.py so both callers share one statement; these tests pin each helper's
contract, and the guard at the bottom keeps inline SQL out of backfill.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod
from models import Section, Status

BACKFILL_PY = Path(__file__).resolve().parents[1] / "scripts" / "backfill.py"
NOW = "2026-07-10 00:00:00"


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _add(conn, item_id, status, *, section=Section.PAPERS, score=None,
         times_competed=0, published_at="2026-07-09T00:00:00Z"):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "published_at, first_seen_date, last_seen_date, status, section, score, "
        "times_competed) VALUES (?, 's', ?, ?, 't', '2026-07-09T00:00:00Z', ?, "
        "'2026-07-09', '2026-07-09', ?, ?, ?, ?)",
        (item_id, f"https://x/{item_id}", f"https://x/{item_id}", published_at,
         status, section, score, times_competed),
    )
    conn.commit()


def _status(conn, item_id):
    return conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()["status"]


def _age_out(conn, **kw):
    return db_mod.age_out_papers_pool(conn, max_competes=7, now=NOW, max_age_days=7, **kw)


def test_age_out_drops_scored_paper_at_competition_cap(conn):
    _add(conn, "capped", Status.CANDIDATE, score=4, times_competed=7)
    _add(conn, "fresh", Status.CANDIDATE, score=4, times_competed=6)
    assert _age_out(conn) == 1
    assert _status(conn, "capped") == Status.DROPPED
    assert _status(conn, "fresh") == Status.CANDIDATE


def test_age_out_marks_never_scored_old_paper_expired_unjudged(conn):
    _add(conn, "old", Status.CANDIDATE, published_at="2026-06-01T00:00:00Z")
    assert _age_out(conn) == 1
    assert _status(conn, "old") == Status.EXPIRED_UNJUDGED


def test_age_out_ignores_other_sections_and_statuses(conn):
    _add(conn, "news", Status.CANDIDATE, section=Section.NEWS, times_competed=9)
    _add(conn, "feat", Status.FEATURED, times_competed=9)
    assert _age_out(conn) == 0
    assert _status(conn, "news") == Status.CANDIDATE
    assert _status(conn, "feat") == Status.FEATURED


def test_age_out_commit_false_leaves_transaction_open(conn):
    _add(conn, "capped", Status.CANDIDATE, score=4, times_competed=7)
    assert _age_out(conn, commit=False) == 1
    assert conn.in_transaction
    conn.rollback()
    assert _status(conn, "capped") == Status.CANDIDATE


def test_mark_candidates_published_only_touches_candidates(conn):
    _add(conn, "c", Status.CANDIDATE)
    _add(conn, "f", Status.FEATURED)
    _add(conn, "other", Status.CANDIDATE)
    assert db_mod.mark_candidates_published(conn, ["c", "f", "missing"], "2026-06-09") == 1
    assert _status(conn, "c") == Status.PUBLISHED
    assert conn.execute(
        "SELECT published_date FROM items WHERE id = 'c'"
    ).fetchone()[0] == "2026-06-09"
    assert conn.execute(
        "SELECT published_date FROM items WHERE id = 'other'"
    ).fetchone()[0] is None
    assert _status(conn, "f") == Status.FEATURED
    assert _status(conn, "other") == Status.CANDIDATE
    assert not conn.in_transaction


def test_mark_candidates_published_empty_is_noop(conn):
    assert db_mod.mark_candidates_published(conn, [], "2026-06-09") == 0


def test_backfill_has_no_inline_execute():
    """backfill.py's item-state SQL lives in db.py."""
    tree = ast.parse(BACKFILL_PY.read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls == [], f"move new backfill SQL into db.py helpers: lines {calls}"
