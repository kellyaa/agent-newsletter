"""Publish seals the items and writes the runs row in ONE transaction (#738).

Sealing (``publish_selected_items``) and the ``runs`` upsert each committed, so
a failure between them left the items ``published`` with no ``runs`` row. The
retry then saw nothing pending, no ``runs`` row, and returned EMPTY_ISSUE
forever; the day never shipped.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

DATE = "2026-07-10"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import db as db_mod

    root = tmp_path / "content"
    monkeypatch.setenv("CONTENT_ROOT", str(root))
    monkeypatch.setenv("EDITION", "agents")
    monkeypatch.setenv("RUN_DATE", DATE)
    db_mod.init_db()
    conn = db_mod.connect()
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, status,"
        " section, first_seen_date, last_seen_date, appearances)"
        " VALUES ('a', 'rss:t', 'https://e.com/a', 'https://e.com/a', 'T',"
        " '2026-07-10T00:00:00Z', 'featured', 'blogs', ?, ?, 1)",
        (DATE, DATE),
    )
    conn.commit()
    conn.close()
    import publish

    issues = publish.issues_dir()
    issues.mkdir(parents=True, exist_ok=True)
    (issues / publish.issue_filename(DATE)).write_text("x" * 600, encoding="utf-8")
    return db_mod


def _state(db_mod):
    conn = db_mod.connect()
    try:
        status = conn.execute("SELECT status FROM items WHERE id='a'").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        conn.close()
    return status, runs


def test_failed_runs_write_rolls_back_the_seal_and_retry_publishes(env, monkeypatch):
    import db as db_mod
    import publish

    real = db_mod._upsert_run_row
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database or disk is full")
        return real(*a, **kw)

    monkeypatch.setattr(db_mod, "_upsert_run_row", flaky)

    with pytest.raises(sqlite3.OperationalError):
        publish.main(DATE)
    assert _state(env) == ("featured", 0)  # day left exactly as it was

    assert publish.main(DATE) == publish.ExitCode.OK
    assert _state(env) == ("published", 1)


def test_publish_day_commits_both_together(env):
    conn = env.connect()
    from publish import run_fields

    n = env.publish_day(conn, DATE, **run_fields({"blogs": 1}, 0, 5))
    conn.close()
    assert n == 1
    assert _state(env) == ("published", 1)


def test_publish_main_does_not_call_the_committing_helpers_separately():
    """publish.py must go through db.publish_day, not publish_selected_items
    plus upsert_run/record_run, which commit separately."""
    tree = ast.parse((SCRIPTS / "publish.py").read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {
        (c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", ""))
        for c in ast.walk(main) if isinstance(c, ast.Call)
    }
    assert "publish_day" in called
    assert not called & {"publish_selected_items", "upsert_run", "record_run"}, called
