"""An empty day fails publish as EMPTY_ISSUE, not MISSING_INPUT.

With nothing featured and nothing in the appendix, ``write`` logs "nothing to
publish" and writes no issue file (exit 0). ``publish`` then found no file and
returned ``MISSING_INPUT`` ("run write.py first"), so ``run_attempts`` recorded
``failed_stage=publish exit_code=2`` and the log told the operator to run a
stage that had already run. ``ExitCode.EMPTY_ISSUE`` exists for exactly this
day, but it was only reachable when an issue file existed with no DB rows.

Publish now tells the two apart: no issue file *and* no featured/appendix rows
is the empty-issue refusal (5); no issue file while rows are waiting is still
a missing input (2). The day still fails, so a retry and the alert behave as
before; only the recorded reason changes.
"""
from __future__ import annotations

import logging

import pytest

DATE = "2026-07-09"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import db as db_mod

    root = tmp_path / "content"
    monkeypatch.setenv("CONTENT_ROOT", str(root))
    monkeypatch.setenv("EDITION", "agents")
    monkeypatch.setenv("RUN_DATE", DATE)
    db_mod.init_db()
    return db_mod


def _item(db_mod, item_id: str, status: str, section: str = "blogs") -> None:
    conn = db_mod.connect()
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, status,"
        " section, first_seen_date, last_seen_date, appearances)"
        " VALUES (?, 'rss:t', ?, ?, 'T', '2026-07-09T00:00:00Z', ?, ?, ?, ?, 1)",
        (item_id, f"https://e.com/{item_id}", f"https://e.com/{item_id}",
         status, section, DATE, DATE),
    )
    conn.commit()
    conn.close()


def test_empty_day_end_to_end_write_then_publish(env, caplog):
    import publish
    import write
    from models import ExitCode

    # A candidate that rank left unscored and a dropped item: neither is
    # featured nor appendix, so the day has nothing to publish.
    _item(env, "c1", "candidate", section="papers")
    _item(env, "d1", "dropped")

    assert write.main(today=DATE) == ExitCode.OK
    assert not (write.issues_dir() / f"{DATE}.md").exists()

    caplog.set_level(logging.ERROR, logger="publish")
    rc = publish.main(today=DATE)
    assert rc == ExitCode.EMPTY_ISSUE
    assert "run write.py first" not in caplog.text
    assert "empty issue" in caplog.text


def test_empty_day_records_no_runs_row_and_seals_nothing(env):
    import publish
    from models import ExitCode

    _item(env, "c1", "candidate", section="papers")
    assert publish.main(today=DATE) == ExitCode.EMPTY_ISSUE

    conn = env.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM items WHERE id='c1'").fetchone()[0] == "candidate"
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["featured", "appendix"])
def test_missing_file_with_selected_items_is_still_missing_input(env, status, caplog):
    import publish
    from models import ExitCode

    _item(env, "x1", status)
    caplog.set_level(logging.ERROR, logger="publish")
    assert publish.main(today=DATE) == ExitCode.MISSING_INPUT
    assert "run write.py first" in caplog.text

    conn = env.connect()
    try:
        # Nothing sealed: the issue was never written.
        assert conn.execute("SELECT status FROM items WHERE id='x1'").fetchone()[0] == status
    finally:
        conn.close()
