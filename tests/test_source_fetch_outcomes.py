"""Per-source fetch outcomes (#572): fetch.main() records one row per source."""
from __future__ import annotations

import sqlite3

import pytest

import db as db_mod
import fetch as fetch_mod
from fetch import Item

RUN_DATE = "2026-07-09"


@pytest.fixture()
def run(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    sources_path = tmp_path / "sources.yaml"
    monkeypatch.setenv("RUN_DATE", RUN_DATE)
    monkeypatch.setattr(fetch_mod, "sources_path", lambda: sources_path)
    monkeypatch.setattr(fetch_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(fetch_mod, "init_db", lambda: db_mod.init_db(db_path))

    def _run(yaml_text, fake_rss):
        sources_path.write_text(yaml_text)
        monkeypatch.setattr(fetch_mod, "fetch_rss", fake_rss)
        rc = fetch_mod.main()
        conn = db_mod.connect(db_path)
        rows = {r["label"]: dict(r) for r in conn.execute(
            "SELECT * FROM source_fetch_outcomes")}
        conn.close()
        return rc, rows

    _run.db_path = db_path
    return _run


def _raising():
    raise RuntimeError("boom")
    yield  # pragma: no cover


def test_success_with_new_items(run):
    rc, rows = run(
        "rss:\n  - id: a\n    url: https://example.com/a.xml\n",
        lambda src, **kw: [Item("rss:a", "https://a.com/1", "T", None, None, None)],
    )
    assert rc == 0
    row = rows["rss/a"]
    assert (row["run_date"], row["family"], row["source_id"]) == (RUN_DATE, "rss", "a")
    assert (row["items_seen"], row["items_new"], row["error"]) == (1, 1, None)


def test_success_with_zero_items_still_recorded(run):
    rc, rows = run(
        "rss:\n  - id: quiet\n    url: https://example.com/q.xml\n"
        "  - id: busy\n    url: https://example.com/b.xml\n",
        lambda src, **kw: [] if src["id"] == "quiet" else [
            Item("rss:busy", "https://b.com/1", "T", None, None, None)],
    )
    assert rc == 0
    assert (rows["rss/quiet"]["items_seen"], rows["rss/quiet"]["error"]) == (0, None)
    assert rows["rss/busy"]["items_seen"] == 1


def test_collector_exception_recorded_as_error(run):
    rc, rows = run(
        "rss:\n  - id: bad\n    url: https://example.com/x.xml\n"
        "  - id: good\n    url: https://example.com/g.xml\n",
        lambda src, **kw: _raising() if src["id"] == "bad" else [
            Item("rss:good", "https://g.com/1", "T", None, None, None)],
    )
    assert rc == 0
    assert rows["rss/bad"]["items_seen"] == 0
    assert rows["rss/bad"]["error"] == "RuntimeError: boom"
    assert rows["rss/good"]["error"] is None


def test_rerun_same_day_replaces_row(tmp_path):
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    kw = dict(items_new=0, fetched_at="t")
    db_mod.record_source_fetch(conn, RUN_DATE, "rss/a", items_seen=0, error="x", **kw)
    db_mod.record_source_fetch(conn, RUN_DATE, "rss/a", items_seen=3, error=None, **kw)
    rows = conn.execute("SELECT items_seen, error FROM source_fetch_outcomes").fetchall()
    assert [tuple(r) for r in rows] == [(3, None)]
    conn.close()


def test_prunes_rows_older_than_90_days(tmp_path):
    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    for d in ("2026-04-09", "2026-04-10", RUN_DATE):  # 91, 90, 0 days old
        db_mod.record_source_fetch(
            conn, d, "rss/a", items_seen=1, items_new=0, error=None, fetched_at="t")
    assert db_mod.prune_source_fetches(conn, RUN_DATE) == 1
    days = [r[0] for r in conn.execute(
        "SELECT run_date FROM source_fetch_outcomes ORDER BY run_date")]
    assert days == ["2026-04-10", RUN_DATE]
    conn.close()


def test_migrates_existing_db_without_table(tmp_path):
    db_path = tmp_path / "old.db"
    db_mod.init_db(db_path)
    raw = sqlite3.connect(db_path)
    raw.execute("DROP TABLE source_fetch_outcomes")
    raw.commit()
    raw.close()
    db_mod.init_db(db_path)
    raw = sqlite3.connect(db_path)
    cols = {r[1] for r in raw.execute("PRAGMA table_info(source_fetch_outcomes)")}
    raw.close()
    assert {"run_date", "label", "family", "source_id", "items_seen",
            "items_new", "error", "fetched_at"} <= cols
