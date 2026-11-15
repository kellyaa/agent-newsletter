"""rank skips once the day's issue file exists, like write does (#728).

write turns rank's verdicts into ``issues/<date>.md`` and skips when that file
exists; publish then seals whatever the DB says. rank used to re-rank anyway,
so the DB and the issue file disagreed:

* publish failed after write, the day was retried: rank reset the featured
  rows (#587) and re-scored them, write skipped, and publish sealed the second
  verdict, not the items the issue shows.
* a finished day was re-run: rank promoted waiting pool papers to featured,
  write skipped, and publish sealed them as published although no issue
  showed them.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from conftest import base_template

import db as db_mod
import publish as publish_mod
import rank as rank_mod
from models import issue_filename

DATE = "2026-07-09"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    content = tmp_path / "content"
    monkeypatch.setenv("CONTENT_ROOT", str(content))
    monkeypatch.setenv("EDITION", "agents")
    monkeypatch.setenv("RUN_DATE", DATE)
    monkeypatch.delenv("LLM_USAGE_LOG", raising=False)
    monkeypatch.delenv("RUN_STARTED_AT", raising=False)
    prompt = tmp_path / "rank.md"
    prompt.write_text(base_template("rank"))
    monkeypatch.setattr(rank_mod, "PROMPT_PATH", prompt)
    db_path = db_mod.db_path()
    db_path.parent.mkdir(parents=True)
    db_mod.init_db(db_path)
    return db_path


def _insert(conn, item_id, section, status="candidate", score=None, tags=None):
    conn.execute(
        """
        INSERT INTO items (
            id, source, url, canonical_url, title, fetched_at, status, section,
            score, tags, why, first_seen_date, last_seen_date, appearances, times_competed
        ) VALUES (?, 'rss:x', 'https://a.com/' || ?, 'https://a.com/' || ?, 'T',
                  '2026-07-09T00:00:00Z', ?, ?, ?, ?, ?, ?, ?, 1, 1)
        """,
        (item_id, item_id, item_id, status, section, score,
         json.dumps(tags) if tags else None, "w" if score is not None else None,
         DATE, DATE),
    )
    conn.commit()


def _statuses(db_path):
    conn = db_mod.connect(db_path)
    try:
        return {r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM items")}
    finally:
        conn.close()


def _write_issue():
    path = rank_mod.issue_path(DATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (publish_mod.MIN_FILE_SIZE_BYTES + 50))
    return path


def _scored(prompt, label):
    sent = json.loads(prompt.split("```json\n")[1].split("\n```")[0])
    return [{"id": it["id"], "score": 9, "tags": [], "why": "ok"} for it in sent]


def test_issue_path_is_the_file_write_and_publish_use():
    import write as write_mod
    assert rank_mod.issue_path(DATE) == write_mod.issues_dir() / issue_filename(DATE)
    assert rank_mod.issue_path(DATE) == publish_mod.issues_dir() / issue_filename(DATE)


def test_ranks_when_no_issue_written(env):
    conn = db_mod.connect(env)
    _insert(conn, "n1", "news")
    conn.close()
    with patch("rank.invoke_ranker", side_effect=_scored) as ranker:
        assert rank_mod.main() == 0
    assert ranker.called
    assert _statuses(env)["n1"] == "featured"


def test_retry_after_publish_failed_keeps_written_featured_set(env):
    conn = db_mod.connect(env)
    for i in range(3):
        _insert(conn, f"n{i}", "news", status="featured", score=9)
    conn.close()
    _write_issue()  # write succeeded with n0..n2; publish then failed
    with patch("rank.invoke_ranker", side_effect=AssertionError("must not re-rank")):
        assert rank_mod.main() == 0
    assert _statuses(env) == {"n0": "featured", "n1": "featured", "n2": "featured"}
    assert publish_mod.main() == 0
    assert set(_statuses(env).values()) == {"published"}


def test_rerun_of_published_day_does_not_seal_pool_papers(env):
    conn = db_mod.connect(env)
    _insert(conn, "shown", "news", status="published", score=9)
    conn.execute("UPDATE items SET published_date = ?", (DATE,))
    _insert(conn, "p1", "papers", score=8, tags=["evals"])  # cleared the bar, lost the cap
    conn.execute("INSERT INTO runs (date, items_featured) VALUES (?, 1)", (DATE,))
    conn.commit()
    conn.close()
    _write_issue()
    with patch("rank.invoke_ranker", side_effect=AssertionError("must not re-rank")):
        assert rank_mod.main() == 0
    assert publish_mod.main() == 0
    assert _statuses(env) == {"shown": "published", "p1": "candidate"}


def test_reset_day_force_lets_rank_run_again(env):
    import reset_day
    conn = db_mod.connect(env)
    _insert(conn, "n1", "news", status="featured", score=9)
    conn.close()
    path = _write_issue()
    assert reset_day.main([]) == 0
    assert not path.exists()
    with patch("rank.invoke_ranker", side_effect=_scored) as ranker:
        assert rank_mod.main() == 0
    assert ranker.called
