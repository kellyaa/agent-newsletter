"""Exact edges of prefilter.age_out_papers(): the competition cap, the age
ceiling, the published_at/fetched_at fallback and the unjudged-vs-dropped split.

Cluster: scripts/prefilter.py age_out_papers + PAPER_POOL_MAX_COMPETES /
PAPER_POOL_MAX_AGE_DAYS (db.PAPER_AGE_OUT_SQL).
Existing tests only probe one day past the ceiling and the cap via backfill.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import db as db_mod
import prefilter as pf_mod

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    c = db_mod.connect(p)
    yield c
    c.close()


def _paper(conn, item_id, *, published_at="2026-07-10T00:00:00Z", fetched_at="2026-07-10T00:00:00Z",
           times_competed=0, score=None, section="papers", status="candidate"):
    conn.execute(
        "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, published_at, "
        "first_seen_date, last_seen_date, status, section, score, times_competed) "
        "VALUES (?, 'arxiv:x', ?, ?, 't', ?, ?, '2026-07-01', '2026-07-01', ?, ?, ?, ?)",
        (item_id, f"https://x/{item_id}", f"https://x/{item_id}", fetched_at, published_at,
         status, section, score, times_competed),
    )
    conn.commit()


def _status(conn, item_id):
    return conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()[0]


def test_pool_constants_are_pinned():
    assert pf_mod.PAPER_POOL_MAX_COMPETES == 7
    assert pf_mod.PAPER_POOL_MAX_AGE_DAYS == 7


def test_competition_cap_is_inclusive(conn):
    _paper(conn, "below", times_competed=pf_mod.PAPER_POOL_MAX_COMPETES - 1, score=5)
    _paper(conn, "at", times_competed=pf_mod.PAPER_POOL_MAX_COMPETES, score=5)
    assert pf_mod.age_out_papers(conn, NOW) == (1, 0)
    assert _status(conn, "below") == "candidate"
    assert _status(conn, "at") == "dropped"


def test_age_ceiling_is_inclusive_to_the_second(conn):
    # NOW is 2026-07-10T12:00:00; exactly 7 days earlier is the ceiling.
    _paper(conn, "exactly-7d", published_at="2026-07-03T12:00:00Z", score=5)
    _paper(conn, "one-second-short", published_at="2026-07-03T12:00:01Z", score=5)
    assert pf_mod.age_out_papers(conn, NOW) == (1, 0)
    assert _status(conn, "exactly-7d") == "dropped"
    assert _status(conn, "one-second-short") == "candidate"


def test_missing_published_at_falls_back_to_fetched_at(conn):
    _paper(conn, "old-fetch", published_at=None, fetched_at="2026-06-01T00:00:00Z", score=5)
    _paper(conn, "new-fetch", published_at=None, fetched_at="2026-07-10T00:00:00Z", score=5)
    assert pf_mod.age_out_papers(conn, NOW) == (1, 0)
    assert _status(conn, "old-fetch") == "dropped"
    assert _status(conn, "new-fetch") == "candidate"


def test_only_never_competed_unscored_papers_expire_unjudged(conn):
    old = "2026-06-01T00:00:00Z"
    _paper(conn, "never-seen", published_at=old, times_competed=0, score=None)
    _paper(conn, "prescored", published_at=old, times_competed=0, score=4)
    _paper(conn, "competed-unscored", published_at=old, times_competed=1, score=None)
    assert pf_mod.age_out_papers(conn, NOW) == (3, 1)
    assert _status(conn, "never-seen") == "expired_unjudged"
    assert _status(conn, "prescored") == "dropped"
    assert _status(conn, "competed-unscored") == "dropped"


def test_expired_unjudged_count_is_this_runs_delta_only(conn):
    _paper(conn, "earlier", status="expired_unjudged", published_at="2026-06-01T00:00:00Z")
    _paper(conn, "today", published_at="2026-06-01T00:00:00Z")
    assert pf_mod.age_out_papers(conn, NOW) == (1, 1)


@pytest.mark.parametrize("section,status", [("news", "candidate"), ("blogs", "candidate"),
                                            ("papers", "featured"), ("papers", "appendix")])
def test_only_papers_candidates_are_aged_out(conn, section, status):
    _paper(conn, "x", section=section, status=status, published_at="2026-06-01T00:00:00Z",
           times_competed=pf_mod.PAPER_POOL_MAX_COMPETES)
    assert pf_mod.age_out_papers(conn, NOW) == (0, 0)
    assert _status(conn, "x") == status
