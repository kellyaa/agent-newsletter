"""db.py helpers behind publish's run finalization.

publish.py used to run its runs-row read, featured-per-section count, the
featured/appendix -> published UPDATE and the runs upsert inline. They now
live in db.py so every pipeline stage reaches state.db through one data
access layer. These tests pin each helper's contract; the guards at the
bottom keep inline SQL and bare section/status literals out of publish.py.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import db as db_mod
from models import Section, Status

PUBLISH_PY = Path(__file__).resolve().parents[1] / "scripts" / "publish.py"
DOMAIN_NAMES = {s.value for s in Section} | {s.value for s in Status}


@pytest.fixture
def conn(tmp_path):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    c = db_mod.connect(path)
    yield c
    c.close()


def _add(conn, item_id, status, *, section=Section.BLOGS, published_date=None):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "first_seen_date, last_seen_date, status, section, appearances, published_date) "
        "VALUES (?, 's', ?, ?, 't', '2026-06-01T00:00:00Z', '2026-06-01', '2026-06-01', "
        "?, ?, 1, ?)",
        (item_id, f"https://x/{item_id}", f"https://x/{item_id}",
         status, section, published_date),
    )
    conn.commit()


def _run_kwargs(**over):
    kw = dict(items_fetched=10, items_candidate=4, items_featured=3,
              items_papers=1, items_news=1, items_blogs=1, notes="appendix=1")
    kw.update(over)
    return kw


def _status(conn, item_id):
    return conn.execute(
        "SELECT status, published_date FROM items WHERE id = ?", (item_id,)
    ).fetchone()


# --- load_run_featured_count -------------------------------------------------

def test_load_run_featured_count_absent_is_none(conn):
    assert db_mod.load_run_featured_count(conn, "2026-06-01") is None


def test_load_run_featured_count_reads_row(conn):
    db_mod.upsert_run(conn, "2026-06-01", **_run_kwargs(items_featured=7))
    assert db_mod.load_run_featured_count(conn, "2026-06-01") == 7
    assert db_mod.load_run_featured_count(conn, "2026-06-02") is None


def test_load_run_featured_count_null_reads_zero(conn):
    conn.execute("INSERT INTO runs (date) VALUES ('2026-06-01')")
    assert db_mod.load_run_featured_count(conn, "2026-06-01") == 0


# --- count_featured_by_section -----------------------------------------------

def test_count_featured_by_section(conn):
    _add(conn, "p1", Status.FEATURED, section=Section.PAPERS)
    _add(conn, "p2", Status.FEATURED, section=Section.PAPERS)
    _add(conn, "n1", Status.FEATURED, section=Section.NEWS)
    _add(conn, "b1", Status.APPENDIX, section=Section.BLOGS)
    _add(conn, "b2", Status.CANDIDATE, section=Section.BLOGS)
    counts = db_mod.count_featured_by_section(conn)
    assert counts == {"papers": 2, "news": 1}
    assert counts.get(Section.PAPERS) == 2
    assert all(type(k) is str for k in counts)


def test_count_featured_by_section_empty(conn):
    assert db_mod.count_featured_by_section(conn) == {}


# --- publish_selected_items --------------------------------------------------

def test_publish_selected_items_promotes_only_selected(conn):
    _add(conn, "f", Status.FEATURED)
    _add(conn, "a", Status.APPENDIX)
    _add(conn, "c", Status.CANDIDATE)
    _add(conn, "old", Status.PUBLISHED, published_date="2026-05-30")
    assert db_mod.publish_selected_items(conn, "2026-06-01") == 2
    assert tuple(_status(conn, "f")) == (Status.PUBLISHED, "2026-06-01")
    assert tuple(_status(conn, "a")) == (Status.PUBLISHED, "2026-06-01")
    assert tuple(_status(conn, "c")) == (Status.CANDIDATE, None)
    assert tuple(_status(conn, "old")) == (Status.PUBLISHED, "2026-05-30")


def test_publish_selected_items_commits(conn, tmp_path):
    _add(conn, "f", Status.FEATURED)
    db_mod.publish_selected_items(conn, "2026-06-01")
    other = db_mod.connect(tmp_path / "state.db")
    try:
        assert other.execute("SELECT status FROM items").fetchone()[0] == Status.PUBLISHED
    finally:
        other.close()


def test_publish_selected_items_noop(conn):
    assert db_mod.publish_selected_items(conn, "2026-06-01") == 0


# --- upsert_run --------------------------------------------------------------

def _run(conn, date="2026-06-01"):
    return dict(conn.execute("SELECT * FROM runs WHERE date = ?", (date,)).fetchone())


def test_upsert_run_inserts_all_columns(conn):
    db_mod.upsert_run(
        conn, "2026-06-01",
        **_run_kwargs(duration_seconds=12, tokens_in=100, tokens_out=50,
                      papers_expired_unjudged=2, papers_prerank_cut=3),
    )
    row = _run(conn)
    assert row == {
        "date": "2026-06-01", "items_fetched": 10, "items_candidate": 4,
        "items_featured": 3, "items_papers": 1, "items_news": 1, "items_blogs": 1,
        "duration_seconds": 12, "tokens_in": 100, "tokens_out": 50,
        "cost_usd": None, "notes": "appendix=1",
        "papers_expired_unjudged": 2, "papers_prerank_cut": 3,
    }


def test_upsert_run_overwrites_counts_but_keeps_nullable_fields(conn):
    db_mod.upsert_run(
        conn, "2026-06-01",
        **_run_kwargs(duration_seconds=12, tokens_in=100, tokens_out=50,
                      papers_expired_unjudged=2, papers_prerank_cut=3),
    )
    db_mod.upsert_run(conn, "2026-06-01",
                      **_run_kwargs(items_featured=9, notes="appendix=0"))
    row = _run(conn)
    assert row["items_featured"] == 9
    assert row["notes"] == "appendix=0"
    assert (row["duration_seconds"], row["tokens_in"], row["tokens_out"]) == (12, 100, 50)
    assert (row["papers_expired_unjudged"], row["papers_prerank_cut"]) == (2, 3)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_upsert_run_commits(conn, tmp_path):
    db_mod.upsert_run(conn, "2026-06-01", **_run_kwargs())
    other = db_mod.connect(tmp_path / "state.db")
    try:
        assert other.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    finally:
        other.close()


# --- guards ------------------------------------------------------------------

def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_publish_has_no_inline_execute():
    """publish.py reaches state.db only through db.py helpers."""
    tree = ast.parse(PUBLISH_PY.read_text(encoding="utf-8"))
    calls = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"execute", "executemany", "executescript"}
    ]
    assert calls == [], f"move new publish SQL into db.py helpers: lines {calls}"


def test_publish_has_no_bare_section_or_status_literals():
    tree = ast.parse(PUBLISH_PY.read_text(encoding="utf-8"))
    skip = _docstring_nodes(tree)
    offenders = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and node.value in DOMAIN_NAMES
    ]
    assert offenders == [], f"use models.Section/Status instead of literals: {offenders}"
