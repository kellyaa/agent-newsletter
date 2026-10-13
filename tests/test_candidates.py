"""Tests for candidates.load_candidates_from_db() (shared prefilter/rank query).

Covers the paths that are only exercised when the caller doesn't manage its
own connection and/or doesn't supply an explicit `now` / `prerank_scorer`:
  - conn=None auto-opens via db.connect() and closes it when done (even on
    the exception path), vs. a caller-supplied conn is never closed.
  - prerank_cap without a prerank_scorer: papers are over the cap but no
    scorer is given, so the cap is requested but the sort/truncate branch
    is skipped — the bucket is returned uncapped.
  - prerank_cap triggers now=None → datetime.now(timezone.utc) default when
    a scorer *is* given (prefilter.py always passes `now`; a direct caller
    of load_candidates_from_db() may not).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


@pytest.fixture()
def db_path(tmp_path):
    import db as db_mod
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p


def _insert_candidate(conn, item_id, section="papers", **overrides):
    row = {
        "id": item_id,
        "source": "arxiv:x",
        "url": f"https://a.com/{item_id}",
        "canonical_url": f"https://a.com/{item_id}",
        "title": f"Paper {item_id}",
        "fetched_at": "2026-07-09T00:00:00Z",
        "status": "candidate",
        "section": section,
        "first_seen_date": "2026-07-09",
        "last_seen_date": "2026-07-09",
        "appearances": 1,
        "published_at": "2026-07-09T00:00:00Z",
        "raw_text": "llm agent evals",
    }
    row.update(overrides)
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT INTO items ({cols}) VALUES ({placeholders})",
        tuple(row.values()),
    )


class TestConnLifecycle:
    def test_conn_none_opens_and_closes_internal_connection(self, db_path, monkeypatch):
        """conn=None auto-opens via db.connect() and closes it afterward."""
        import candidates
        import db as db_mod

        real_connect = db_mod.connect
        opened = []

        def tracking_connect(path=db_path):
            c = real_connect(path)
            opened.append(c)
            return c

        monkeypatch.setattr(candidates, "connect", tracking_connect)

        result = candidates.load_candidates_from_db()

        assert isinstance(result, dict)
        assert len(opened) == 1
        # sqlite3 raises ProgrammingError on any operation against a closed
        # connection — this is the simplest externally-observable proof the
        # auto-opened connection was closed.
        with pytest.raises(Exception):
            opened[0].execute("SELECT 1")

    def test_caller_supplied_conn_is_never_closed(self, db_path):
        """A caller-supplied conn is returned usable; load_candidates_from_db
        must not close it."""
        import candidates
        import db as db_mod

        conn = db_mod.connect(db_path)
        try:
            result = candidates.load_candidates_from_db(conn)
            assert isinstance(result, dict)
            # Still usable — proves it wasn't closed internally.
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()

    def test_conn_none_closes_even_when_load_raises(self, db_path, monkeypatch):
        """The internal connection is closed even if _load() raises."""
        import candidates
        import db as db_mod

        real_connect = db_mod.connect
        opened = []

        def tracking_connect(path=db_path):
            c = real_connect(path)
            opened.append(c)
            return c

        monkeypatch.setattr(candidates, "connect", tracking_connect)

        def boom(conn, prerank_cap, prerank_scorer, now):
            raise RuntimeError("boom")

        monkeypatch.setattr(candidates, "_load", boom)

        with pytest.raises(RuntimeError, match="boom"):
            candidates.load_candidates_from_db()

        assert len(opened) == 1
        with pytest.raises(Exception):
            opened[0].execute("SELECT 1")


class TestPrerankCapWithoutScorer:
    def test_cap_requested_without_scorer_leaves_bucket_uncapped(self, db_path):
        """prerank_cap alone (no prerank_scorer) is a no-op on the bucket:
        the cap-exceeded branch is entered but the sort/truncate step is
        skipped because there's nothing to sort by."""
        import candidates
        import db as db_mod

        conn = db_mod.connect(db_path)
        for i in range(5):
            _insert_candidate(conn, f"p{i}")
        conn.commit()

        result = candidates.load_candidates_from_db(conn, prerank_cap=3)
        conn.close()

        assert len(result["papers"]) == 5


class TestPrerankCapDefaultNow:
    def test_now_none_defaults_to_current_utc_time(self, db_path):
        """When prerank_cap + prerank_scorer are given but now=None, the
        cap path falls back to datetime.now(timezone.utc)."""
        import candidates
        import db as db_mod

        conn = db_mod.connect(db_path)
        for i in range(5):
            _insert_candidate(conn, f"p{i}")
        conn.commit()

        fixed_now = datetime(2026, 7, 9, 12, 0, 0, tzinfo=timezone.utc)

        def scorer(item, now):
            # Prove *some* now was threaded through, without depending on
            # it being wall-clock-exact: it must be an aware UTC datetime.
            assert now.tzinfo is not None
            return 0.0

        with patch("candidates.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = candidates.load_candidates_from_db(
                conn, prerank_cap=3, prerank_scorer=scorer,
            )
        conn.close()

        mock_dt.now.assert_called_once_with(timezone.utc)
        assert len(result["papers"]) == 3
