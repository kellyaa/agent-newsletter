"""``backfill --apply-published`` seals the rows the issue shows, as publish does (#748).

The daily run seals every ``featured`` and ``appendix`` row (``db.publish_selected_items``):
exactly what write put in the issue file. Backfill used to seal only the ids the
ranker marked ``featured``, so a mid-band paper (``appendix_min <= score <
featured_min``) appeared in the backfilled issue's appendix but stayed
``candidate`` in the live pool, free to land in a later issue too.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys

import backfill
import db as db_mod
import write as write_mod
from models import Status

DATE = "2026-07-15"


def _paper(conn, iid: str, score: int, status: str = Status.CANDIDATE) -> None:
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, author, published_at, "
        "fetched_at, raw_text, status, section, score, tags, why, first_seen_date, "
        "last_seen_date, appearances, keyword_gate_bypass, times_competed) "
        "VALUES (?, 'arxiv:cs', ?, ?, ?, 'A', '2026-07-14', '2026-07-14', 'abs', ?, "
        "'papers', ?, '[]', 'w', '2026-07-14', '2026-07-14', 1, 0, 0)",
        (iid, f"http://arxiv.org/{iid}", f"http://arxiv.org/{iid}", f"Paper {iid}",
         status, score),
    )


def _statuses(path) -> dict[str, tuple[str, str | None]]:
    conn = sqlite3.connect(path)
    try:
        return {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT id, status, published_date FROM items")}
    finally:
        conn.close()


def _run_backfill(tmp_path, monkeypatch, *rows: tuple[str, int]):
    """Live DB with prescored papers ``rows``; sandbox is a copy; real rank + write."""
    live = tmp_path / "live.db"
    db_mod.init_db(live)
    conn = sqlite3.connect(live)
    for iid, score in rows:
        _paper(conn, iid, score)
    conn.commit()
    conn.close()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    shutil.copy(live, sandbox / "state.db")

    issues = tmp_path / "issues"
    issues.mkdir()
    monkeypatch.setattr(backfill, "setup_sandbox", lambda date: sandbox)
    monkeypatch.setattr(db_mod, "db_path", lambda: live)
    monkeypatch.setattr(write_mod, "issues_dir", lambda: issues)

    def fake_call_writer(today, featured, *a, **kw):
        return {"theme": "t", "items": [{"id": it["id"], "summary": "s"} for it in featured]}

    monkeypatch.setattr(write_mod, "call_writer", fake_call_writer)
    monkeypatch.setattr(sys, "argv", ["backfill.py", "--date", DATE, "--apply-published"])
    assert backfill.main() == 0
    return live, (issues / f"{DATE}.md").read_text()


def test_appendix_paper_in_backfilled_issue_is_sealed_in_live_db(tmp_path, monkeypatch):
    # agents edition papers: featured_min 7, appendix_min 5.
    live, issue = _run_backfill(tmp_path, monkeypatch, ("top", 9), ("mid", 6), ("low", 2))

    # The issue shows top as featured and mid in the appendix...
    assert 'id: "top"' in issue and 'id: "mid"' in issue
    assert issue.index('id: "mid"') > issue.index("appendix:")
    # ...so both are sealed in the live DB with the issue's date, as publish would.
    after = _statuses(live)
    assert after["top"] == (Status.PUBLISHED, DATE)
    assert after["mid"] == (Status.PUBLISHED, DATE)
    # Rows the issue does not show are left alone.
    assert after["low"] == (Status.CANDIDATE, None)


def test_load_issue_item_ids_is_what_publish_seals(tmp_path):
    """The backfill reader and the daily seal use one definition of 'in the issue'."""
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    conn = db_mod.connect(path)
    for iid, status in (("f", Status.FEATURED), ("a", Status.APPENDIX),
                        ("c", Status.CANDIDATE), ("d", Status.DROPPED),
                        ("p", Status.PUBLISHED)):
        _paper(conn, iid, 5, status)
    conn.commit()
    ids = db_mod.load_issue_item_ids(conn)
    assert ids == ["a", "f"]
    db_mod.publish_selected_items(conn, DATE)
    sealed = sorted(r[0] for r in conn.execute(
        "SELECT id FROM items WHERE published_date = ?", (DATE,)))
    conn.close()
    assert sealed == ids
    assert set(db_mod.ISSUE_STATUSES) == {Status.FEATURED, Status.APPENDIX}
