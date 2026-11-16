"""Publish seals the items and writes the ``runs`` row in one transaction (#738).

Before, ``publish_selected_items`` and ``upsert_run`` each committed. A failure
between them left the items ``published`` with no ``runs`` row, so every retry
hit EMPTY_ISSUE and the day never shipped.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

import db as db_mod
import publish as publish_mod

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DAY = "2026-06-01"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    issues = tmp_path / "issues"
    issues.mkdir()
    (issues / f"{DAY}.md").write_text("x" * (publish_mod.MIN_FILE_SIZE_BYTES + 50))
    monkeypatch.setattr(publish_mod, "issues_dir", lambda: issues)
    monkeypatch.setattr(publish_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(publish_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setenv("RUN_DATE", DAY)
    monkeypatch.delenv("LLM_USAGE_LOG", raising=False)
    monkeypatch.delenv("RUN_STARTED_AT", raising=False)
    conn = db_mod.connect(db_path)
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, "
        "status, section, first_seen_date, last_seen_date, appearances) "
        "VALUES ('f1', 'rss:t', 'https://e.com/1', 'https://e.com/1', 'T', "
        "'2026-06-01T00:00:00Z', 'featured', 'news', ?, ?, 1)",
        (DAY, DAY),
    )
    conn.commit()
    conn.close()
    return db_path


def _state(db_path):
    conn = db_mod.connect(db_path)
    try:
        status = conn.execute("SELECT status FROM items WHERE id = 'f1'").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        conn.close()
    return status, runs


def test_runs_write_failure_leaves_day_unpublished_and_retry_ships(env, monkeypatch):
    real = db_mod.upsert_run
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database or disk is full")
        return real(*a, **kw)

    monkeypatch.setattr(db_mod, "upsert_run", flaky)
    monkeypatch.setattr(publish_mod, "upsert_run", flaky)

    with pytest.raises(sqlite3.OperationalError):
        publish_mod.main()
    assert _state(env) == ("featured", 0)

    assert publish_mod.main() == 0
    assert _state(env) == ("published", 1)


def test_usage_failure_before_writing_leaves_day_unpublished(env, monkeypatch):
    def boom(_path):
        raise OSError("usage log unreadable")

    monkeypatch.setattr(publish_mod, "usage_totals", boom)
    with pytest.raises(OSError):
        publish_mod.main()
    assert _state(env) == ("featured", 0)



def test_seal_and_record_run_rolls_back_the_seal(env, monkeypatch):
    conn = db_mod.connect(env)
    monkeypatch.setattr(
        db_mod, "upsert_run",
        lambda *a, **kw: (_ for _ in ()).throw(sqlite3.OperationalError("x")),
    )
    with pytest.raises(sqlite3.OperationalError):
        db_mod.seal_and_record_run(conn, DAY, notes="n")
    conn.close()
    assert _state(env) == ("featured", 0)


def test_seal_and_record_run_commits_both(env):
    conn = db_mod.connect(env)
    sealed = db_mod.seal_and_record_run(
        conn, DAY, items_fetched=1, items_candidate=1, items_featured=1,
        items_papers=0, items_news=1, items_blogs=0, notes="n",
    )
    conn.close()
    assert sealed == 1
    assert _state(env) == ("published", 1)


def test_commit_false_defers_the_commit(env):
    conn = db_mod.connect(env)
    db_mod.publish_selected_items(conn, DAY, commit=False)
    conn.rollback()
    conn.close()
    assert _state(env) == ("featured", 0)


def test_publish_does_not_call_the_committing_helpers_separately():
    """publish.py must go through db.seal_and_record_run, never the two
    committing helpers one after the other."""
    tree = ast.parse((SCRIPTS / "publish.py").read_text())
    names = {
        n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
    } | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    called = {
        (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None))
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    }
    assert "seal_and_record_run" in called
    assert "publish_selected_items" not in names
    # upsert_run is only allowed inside record_run, which main() never calls.
    main_fn = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
    )
    in_main = {
        (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None))
        for n in ast.walk(main_fn) if isinstance(n, ast.Call)
    }
    assert "upsert_run" not in in_main
    assert "record_run" not in in_main
