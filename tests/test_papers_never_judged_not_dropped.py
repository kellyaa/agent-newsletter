"""Regression tests for issues #285 and #289.

Papers that the ``PAPER_PRERANK_CAP`` keeps out of every ranker call must not
age out of the pool as ``dropped`` with ``score IS NULL``: a never-judged expiry
is indistinguishable from an editorial rejection (#285). They do still leave the
pool once stale, with the distinct status ``expired_unjudged``, so the pool
can't grow without bound (#289).

Runs the real ``prefilter.main()`` followed by the real ``rank.main()`` against
a temp DB (only the LLM call, ``rank.invoke_ranker``, is mocked).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from conftest import base_template

FROZEN = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)
FRESH = "2026-07-09T06:00:00Z"
STALE = "2026-07-02T08:00:00Z"  # 7.17 days before FROZEN, past the 7-day ceiling


def _frozen_datetime():
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN if tz is None else FROZEN.astimezone(tz)

    return _Frozen


def _insert_paper(conn, item_id, published_at, title, raw_text):
    conn.execute(
        """
        INSERT INTO items (
            id, source, url, canonical_url, title, fetched_at, status, section,
            score, first_seen_date, last_seen_date, appearances,
            times_competed, published_at, raw_text
        ) VALUES (?, 'arxiv:cs.AI', 'https://example.com/' || ?,
                  'https://example.com/' || ?, ?, ?, 'candidate', 'papers',
                  NULL, '2026-07-02', '2026-07-09', 1, 0, ?, ?)
        """,
        (item_id, item_id, item_id, title, published_at, published_at, raw_text),
    )


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    import candidates as candidates_mod
    import db as db_mod
    import prefilter as pf_mod
    import rank as rank_mod

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)

    rubric = tmp_path / "rank.md"
    rubric.write_text(base_template("rank"))
    rubric_hash = tmp_path / ".rubric_hash"

    def connect(*_a, **_kw):
        return db_mod.connect(db_path)

    monkeypatch.setattr(pf_mod, "CANDIDATES_OUT", tmp_path / "candidates.json")
    monkeypatch.setattr(pf_mod, "RUBRIC_PATH", rubric)
    monkeypatch.setattr(pf_mod, "RUBRIC_HASH_PATH", rubric_hash)
    rubric_hash.write_text(pf_mod._rubric_hash())
    monkeypatch.setattr(pf_mod, "connect", connect)
    monkeypatch.setattr(pf_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(rank_mod, "PROMPT_PATH", rubric)
    monkeypatch.setattr(rank_mod, "RANKED_PATH", tmp_path / "ranked.json")
    monkeypatch.setattr(rank_mod, "connect", connect)
    monkeypatch.setattr(rank_mod, "init_db", lambda: db_mod.init_db(db_path))
    # Real load_candidates_from_db() is used (not monkeypatched); only its
    # connection factory and clock are pointed at the test DB / frozen time.
    monkeypatch.setattr(candidates_mod, "connect", connect)
    monkeypatch.setattr(pf_mod, "datetime", _frozen_datetime())
    monkeypatch.setattr(candidates_mod, "datetime", _frozen_datetime())
    return db_path


def _fake_ranker(sent):
    def invoke(prompt, label):
        items = json.loads(prompt.split("```json\n")[1].split("\n```")[0])
        sent.extend(it["id"] for it in items)
        return [{"id": it["id"], "score": 5, "tags": [], "why": "ok"} for it in items]

    return invoke


def test_pool_over_cap_does_not_mark_unscored_stale_papers_dropped(wired):
    import db as db_mod
    import prefilter as pf_mod
    import rank as rank_mod
    from unittest.mock import patch

    assert pf_mod.PAPER_PRERANK_CAP == 100

    conn = db_mod.connect(wired)
    for i in range(pf_mod.PAPER_PRERANK_CAP):
        _insert_paper(conn, f"fresh{i:02d}", FRESH,
                      "LLM agent tool use planning", "agent workflow evaluation")
    # Older and keyword-poor => the three lowest _prerank_score values, so the
    # cap leaves them out of the ranker call.
    stale_ids = [f"stale{i}" for i in range(3)]
    for sid in stale_ids:
        _insert_paper(conn, sid, STALE, "Agent paper", "agent")
    conn.commit()
    conn.close()

    assert pf_mod.main() == 0

    # #297: prefilter staged this run's counters for publish to copy onto `runs`.
    conn = db_mod.connect(wired)
    staged = db_mod.load_stage_stats(conn, "2026-07-09")
    conn.close()
    assert staged == {"papers_expired_unjudged": 3, "papers_prerank_cut": 0}

    sent: list[str] = []
    with patch("rank.invoke_ranker", side_effect=_fake_ranker(sent)):
        assert rank_mod.main() == 0

    # The cap really did truncate the pool, and the stale papers were what it cut.
    assert len(sent) == pf_mod.PAPER_PRERANK_CAP
    assert not set(stale_ids) & set(sent)

    conn = db_mod.connect(wired)
    unjudged_dropped = conn.execute(
        "SELECT id FROM items WHERE section='papers' "
        "AND status='dropped' AND score IS NULL"
    ).fetchall()
    stale_rows = conn.execute(
        f"SELECT status, score FROM items WHERE id IN ({','.join('?' * 3)})",
        stale_ids,
    ).fetchall()
    conn.close()

    assert [r["id"] for r in unjudged_dropped] == []
    assert all(r["status"] == "expired_unjudged" and r["score"] is None for r in stale_rows)

    # The fresh papers were judged, not expired.
    conn = db_mod.connect(wired)
    fresh_expired = conn.execute(
        "SELECT COUNT(*) FROM items WHERE id LIKE 'fresh%' AND status='expired_unjudged'"
    ).fetchone()[0]
    conn.close()
    assert fresh_expired == 0


def test_fresh_unscored_paper_stays_in_pool(wired):
    """Never-competed papers inside the recency window are untouched."""
    import db as db_mod
    import prefilter as pf_mod

    conn = db_mod.connect(wired)
    _insert_paper(conn, "fresh-unscored", FRESH, "Agent paper", "agent")
    conn.commit()
    conn.close()

    pf_mod.main()

    conn = db_mod.connect(wired)
    row = conn.execute("SELECT status FROM items WHERE id='fresh-unscored'").fetchone()
    conn.close()
    assert row["status"] == "candidate"


def test_stale_paper_that_has_competed_still_ages_out(wired):
    """The recency ceiling is kept for papers that were actually judged."""
    import db as db_mod
    import prefilter as pf_mod

    conn = db_mod.connect(wired)
    _insert_paper(conn, "judged", STALE, "Agent paper", "agent")
    conn.execute("UPDATE items SET times_competed = 1, score = 5 WHERE id='judged'")
    conn.commit()
    conn.close()

    pf_mod.main()

    conn = db_mod.connect(wired)
    row = conn.execute("SELECT status FROM items WHERE id='judged'").fetchone()
    conn.close()
    assert row["status"] == "dropped"


def test_prerank_cut_and_expired_counts_are_logged_and_staged(wired, caplog):
    """#297: every run logs both counts and stages them in run_stage_stats."""
    import logging

    import db as db_mod
    import prefilter as pf_mod

    conn = db_mod.connect(wired)
    for i in range(pf_mod.PAPER_PRERANK_CAP + 4):
        _insert_paper(conn, f"fresh{i:03d}", FRESH, "LLM agent tool use", "agent")
    _insert_paper(conn, "stale0", STALE, "Agent paper", "agent")
    conn.commit()
    conn.close()

    with caplog.at_level(logging.INFO):
        assert pf_mod.main() == 0

    text = caplog.text
    assert "papers age-out this run: expired_unjudged=1 dropped=0" in text
    assert "papers pre-rank cap cut this run: 4" in text

    conn = db_mod.connect(wired)
    staged = db_mod.load_stage_stats(conn, "2026-07-09")
    conn.close()
    assert staged == {"papers_expired_unjudged": 1, "papers_prerank_cut": 4}


def test_counts_are_logged_even_when_zero(wired, caplog):
    import logging

    import prefilter as pf_mod

    with caplog.at_level(logging.INFO):
        assert pf_mod.main() == 0

    assert "papers age-out this run: expired_unjudged=0 dropped=0" in caplog.text
    assert "papers pre-rank cap cut this run: 0" in caplog.text
