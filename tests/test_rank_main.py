"""Integration tests for rank.main() pipeline.

Tests the DB-level flow with invoke_ranker mocked out.
Covers:
  - return 2 when candidates.json missing
  - return 2 when prompt file missing
  - return 0 when no candidates (total==0)
  - idempotent resume (already-ranked items skipped)
  - prescored papers bypass LLM, unscored papers call invoke_ranker
  - fallback to appendix for items LLM omits
  - times_competed bump for papers that remain candidate after ranking
  - decisions written to DB via persist()
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    import db as db_mod
    p = tmp_path / "state.db"
    db_mod.init_db(p)
    return p


@pytest.fixture()
def candidates_path(tmp_path):
    return tmp_path / "candidates.json"


@pytest.fixture()
def prompt_path(tmp_path):
    p = tmp_path / "rank.md"
    p.write_text("# Rank rubric\nScore papers by relevance.")
    return p


def _write_candidates(candidates_path, papers=None, prescored=None, news=None, blogs=None):
    data = {
        "papers": papers or [],
        "papers_prescored": prescored or [],
        "news": news or [],
        "blogs": blogs or [],
    }
    candidates_path.write_text(json.dumps(data))


def _insert_candidate(conn, item_id, section="papers", status="candidate", times_competed=0):
    conn.execute(
        """
        INSERT OR REPLACE INTO items (
            id, source, url, canonical_url, title, fetched_at,
            status, section, first_seen_date, last_seen_date, appearances, times_competed
        ) VALUES (?, 'arxiv:x', 'https://a.com/' || ?, 'https://a.com/' || ?,
                  'Title', '2026-07-09T00:00:00Z',
                  ?, ?, '2026-07-09', '2026-07-09', 1, ?)
        """,
        (item_id, item_id, item_id, status, section, times_competed),
    )
    conn.commit()


def _run_main(db_path, candidates_path, prompt_path, monkeypatch):
    import rank as rank_mod
    import db as db_mod

    monkeypatch.setattr(rank_mod, "CANDIDATES_PATH", candidates_path)
    monkeypatch.setattr(rank_mod, "PROMPT_PATH", prompt_path)
    monkeypatch.setattr(rank_mod, "RANKED_PATH", candidates_path.parent / "ranked.json")
    monkeypatch.setattr(rank_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(rank_mod, "init_db", lambda: db_mod.init_db(db_path))

    return rank_mod.main()


# ---------------------------------------------------------------------------
# main() — guard conditions
# ---------------------------------------------------------------------------

class TestRankMainGuards:
    def test_returns_2_when_candidates_missing(self, db_path, candidates_path, prompt_path, monkeypatch):
        # candidates_path not written → file doesn't exist
        result = _run_main(db_path, candidates_path, prompt_path, monkeypatch)
        assert result == 2

    def test_returns_2_when_prompt_missing(self, db_path, candidates_path, prompt_path, monkeypatch):
        _write_candidates(candidates_path)
        # Remove prompt file
        import rank as rank_mod
        import db as db_mod
        monkeypatch.setattr(rank_mod, "CANDIDATES_PATH", candidates_path)
        monkeypatch.setattr(rank_mod, "PROMPT_PATH", candidates_path.parent / "missing_rank.md")
        monkeypatch.setattr(rank_mod, "RANKED_PATH", candidates_path.parent / "ranked.json")
        monkeypatch.setattr(rank_mod, "connect", lambda: db_mod.connect(db_path))
        monkeypatch.setattr(rank_mod, "init_db", lambda: db_mod.init_db(db_path))
        result = rank_mod.main()
        assert result == 2

    def test_returns_0_when_no_candidates(self, db_path, candidates_path, prompt_path, monkeypatch):
        _write_candidates(candidates_path)  # all empty
        result = _run_main(db_path, candidates_path, prompt_path, monkeypatch)
        assert result == 0


# ---------------------------------------------------------------------------
# main() — prescored papers bypass LLM
# ---------------------------------------------------------------------------

class TestRankMainPrescored:
    def test_prescored_papers_no_llm_call(self, db_path, candidates_path, prompt_path, monkeypatch):
        """Prescored papers should go through assign_statuses without calling invoke_ranker."""
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_candidate(conn, "pp1", section="papers")
        conn.close()

        _write_candidates(candidates_path, prescored=[
            {"id": "pp1", "score": 8, "tags": ["evals"], "why": "good"}
        ])

        with patch("rank.invoke_ranker") as mock_ranker:
            result = _run_main(db_path, candidates_path, prompt_path, monkeypatch)

        assert result == 0
        mock_ranker.assert_not_called()

    def test_prescored_papers_written_to_db(self, db_path, candidates_path, prompt_path, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_candidate(conn, "pp1", section="papers")
        conn.close()

        _write_candidates(candidates_path, prescored=[
            {"id": "pp1", "score": 8, "tags": ["evals"], "why": "good"}
        ])

        with patch("rank.invoke_ranker"):
            _run_main(db_path, candidates_path, prompt_path, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT score, status FROM items WHERE id='pp1'").fetchone()
        conn.close()
        assert row["score"] == 8
        assert row["status"] in ("featured", "appendix", "dropped", "candidate")


# ---------------------------------------------------------------------------
# main() — unscored papers call invoke_ranker
# ---------------------------------------------------------------------------

class TestRankMainUnscored:
    def test_unscored_papers_call_invoke_ranker(self, db_path, candidates_path, prompt_path, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_candidate(conn, "up1", section="papers")
        conn.close()

        _write_candidates(candidates_path, papers=[
            {"id": "up1", "title": "Paper", "source": "arxiv:x", "url": "https://a.com/up1",
             "author": None, "published_at": None, "raw_text": None}
        ])

        mock_result = [{"id": "up1", "score": 7, "tags": ["research"], "why": "relevant"}]
        with patch("rank.invoke_ranker", return_value=mock_result) as mock_ranker:
            result = _run_main(db_path, candidates_path, prompt_path, monkeypatch)

        assert result == 0
        mock_ranker.assert_called_once()
        # Confirm label="papers" was used
        assert mock_ranker.call_args[1].get("label") == "papers" or \
               mock_ranker.call_args[0][1] == "papers"

    def test_unscored_papers_status_written(self, db_path, candidates_path, prompt_path, monkeypatch):
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_candidate(conn, "up1", section="papers")
        conn.close()

        _write_candidates(candidates_path, papers=[
            {"id": "up1", "title": "Paper", "source": "arxiv:x", "url": "https://a.com/up1",
             "author": None, "published_at": None, "raw_text": None}
        ])

        mock_result = [{"id": "up1", "score": 9, "tags": [], "why": "top"}]
        with patch("rank.invoke_ranker", return_value=mock_result):
            _run_main(db_path, candidates_path, prompt_path, monkeypatch)

        conn = db_mod.connect(db_path)
        row = conn.execute("SELECT status, score FROM items WHERE id='up1'").fetchone()
        conn.close()
        assert row["score"] == 9


# ---------------------------------------------------------------------------
# main() — idempotent resume (already-ranked items skipped)
# ---------------------------------------------------------------------------

class TestRankMainIdempotentResume:
    def test_already_ranked_items_skipped(self, db_path, candidates_path, prompt_path, monkeypatch):
        """Items no longer in 'candidate' status are filtered from the LLM input."""
        import db as db_mod
        conn = db_mod.connect(db_path)
        # One item already ranked (featured), one still candidate
        _insert_candidate(conn, "done", section="papers", status="featured")
        _insert_candidate(conn, "todo", section="papers", status="candidate")
        conn.close()

        _write_candidates(candidates_path, papers=[
            {"id": "done", "title": "P1", "source": "arxiv:x", "url": "https://a.com/done",
             "author": None, "published_at": None, "raw_text": None},
            {"id": "todo", "title": "P2", "source": "arxiv:x", "url": "https://a.com/todo",
             "author": None, "published_at": None, "raw_text": None},
        ])

        sent_items = []
        def mock_invoke(prompt, label):
            # Capture which items were sent to the ranker
            items = json.loads(prompt.split("```json\n")[1].split("\n```")[0])
            sent_items.extend([it["id"] for it in items])
            return [{"id": "todo", "score": 7, "tags": [], "why": "ok"}]

        with patch("rank.invoke_ranker", side_effect=mock_invoke):
            _run_main(db_path, candidates_path, prompt_path, monkeypatch)

        assert "todo" in sent_items
        assert "done" not in sent_items

    def test_all_already_ranked_returns_0(self, db_path, candidates_path, prompt_path, monkeypatch):
        """If all items are already past candidate status, main() exits cleanly."""
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_candidate(conn, "done1", section="papers", status="featured")
        _insert_candidate(conn, "done2", section="news", status="appendix")
        conn.close()

        _write_candidates(candidates_path,
            papers=[{"id": "done1", "title": "P", "source": "arxiv:x",
                     "url": "https://a.com/d1", "author": None, "published_at": None, "raw_text": None}],
            news=[{"id": "done2", "title": "N", "source": "hn:x",
                   "url": "https://a.com/d2", "author": None, "published_at": None, "raw_text": None}],
        )

        with patch("rank.invoke_ranker") as mock_ranker:
            result = _run_main(db_path, candidates_path, prompt_path, monkeypatch)

        assert result == 0
        mock_ranker.assert_not_called()


# ---------------------------------------------------------------------------
# main() — fallback for unscored candidates
# ---------------------------------------------------------------------------

class TestRankMainFallback:
    def test_missing_id_in_ranker_response_gets_appendix_fallback(
        self, db_path, candidates_path, prompt_path, monkeypatch
    ):
        """If ranker omits an id it was sent, that item falls back to appendix/score=0."""
        import db as db_mod
        conn = db_mod.connect(db_path)
        _insert_candidate(conn, "scored", section="news")
        _insert_candidate(conn, "missed", section="news")
        conn.close()

        news_items = [
            {"id": "scored", "title": "N1", "source": "hn:x", "url": "https://a.com/s",
             "author": None, "published_at": None, "raw_text": None},
            {"id": "missed", "title": "N2", "source": "hn:x", "url": "https://a.com/m",
             "author": None, "published_at": None, "raw_text": None},
        ]
        _write_candidates(candidates_path, news=news_items)

        # Ranker only returns one of the two items
        with patch("rank.invoke_ranker", return_value=[
            {"id": "scored", "score": 7, "tags": [], "why": "ok"}
        ]):
            _run_main(db_path, candidates_path, prompt_path, monkeypatch)

        conn = db_mod.connect(db_path)
        missed_row = conn.execute("SELECT status, score FROM items WHERE id='missed'").fetchone()
        conn.close()
        assert missed_row["status"] == "appendix"
        assert missed_row["score"] == 0


# ---------------------------------------------------------------------------
# main() — times_competed bump for papers
# ---------------------------------------------------------------------------

class TestRankMainTimesCompeted:
    def test_papers_remaining_candidate_get_times_competed_bumped(
        self, db_path, candidates_path, prompt_path, monkeypatch
    ):
        """Papers that score >= featured_min but lose the cap stay 'candidate'
        and should have times_competed incremented."""
        from rank import SECTION_RULES
        import db as db_mod

        rules = SECTION_RULES["papers"]
        cap = rules["cap"]
        featured_min = rules["featured_min"]

        conn = db_mod.connect(db_path)
        # Insert cap+2 papers at featured_min score, all start with times_competed=0
        all_ids = [f"p{i}" for i in range(cap + 2)]
        for pid in all_ids:
            _insert_candidate(conn, pid, section="papers", times_competed=0)
        conn.close()

        paper_items = [
            {"id": pid, "title": f"P{i}", "source": "arxiv:x",
             "url": f"https://a.com/{pid}", "author": None, "published_at": None, "raw_text": None}
            for i, pid in enumerate(all_ids)
        ]
        _write_candidates(candidates_path, papers=paper_items)

        # Ranker gives featured_min score to all → cap wins featured, rest stay candidate
        mock_rankings = [
            {"id": pid, "score": featured_min, "tags": [], "why": "ok"}
            for pid in all_ids
        ]
        with patch("rank.invoke_ranker", return_value=mock_rankings):
            _run_main(db_path, candidates_path, prompt_path, monkeypatch)

        conn = db_mod.connect(db_path)
        rows = {
            r["id"]: r
            for r in conn.execute("SELECT id, status, times_competed FROM items").fetchall()
        }
        conn.close()

        # Items that remain 'candidate' should have times_competed == 1
        candidates_remaining = [pid for pid in all_ids if rows[pid]["status"] == "candidate"]
        assert len(candidates_remaining) == 2  # cap+2 - cap = 2
        for pid in candidates_remaining:
            assert rows[pid]["times_competed"] == 1

        # Items that became 'featured' should NOT have times_competed bumped
        featured_items = [pid for pid in all_ids if rows[pid]["status"] == "featured"]
        assert len(featured_items) == cap
        for pid in featured_items:
            assert rows[pid]["times_competed"] == 0  # gated on status='candidate'
