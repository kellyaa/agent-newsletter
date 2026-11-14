"""An unscored paper stays in the pool; only news/blogs fall back to the appendix.

``rank.score_candidates`` used to seal every candidate the ranker did not
score as ``appendix`` with score 0. For papers that is terminal: publish seals
appendix rows (#704) and rank's retry reset only touches ``featured`` rows
(#587). So one failed papers call (``RuntimeError``, isolated per #208), one
content-filtered paper, or one omitted id lost those papers for good, and a
retry of the day could not score them again. That contradicted
``rank_section``'s own contract ("unscored papers re-compete") and the papers
pool rules, where a never-scored paper only leaves the pool as
``expired_unjudged`` (#289). These tests pin the pool behaviour (#720).
"""
from __future__ import annotations

import json
from unittest.mock import patch

from models import Section, Status
from test_rank_main import _insert_candidate, _run_main, db_path, prompt_path  # noqa: F401


def _rows(db_path):
    import db as db_mod
    conn = db_mod.connect(db_path)
    rows = {r["id"]: dict(r) for r in conn.execute(
        "SELECT id, status, score, times_competed FROM items")}
    conn.close()
    return rows


def _scores_everything(prompt, label):
    sent = json.loads(prompt.split("```json\n")[1].split("\n```")[0])
    return [{"id": it["id"], "score": 9, "tags": [], "why": "ok"} for it in sent]


def test_failed_papers_call_leaves_papers_unscored_in_the_pool(db_path, prompt_path, monkeypatch):
    import db as db_mod
    conn = db_mod.connect(db_path)
    for i in range(3):
        _insert_candidate(conn, f"p{i}", section="papers")
    _insert_candidate(conn, "n0", section="news")
    conn.close()

    def papers_fail(prompt, label):
        if label == Section.PAPERS:
            raise RuntimeError("llm failed after 2 attempts for papers")
        return _scores_everything(prompt, label)

    with patch("rank.invoke_ranker", side_effect=papers_fail):
        assert _run_main(db_path, prompt_path, monkeypatch) == 0

    rows = _rows(db_path)
    for pid in ("p0", "p1", "p2"):
        assert rows[pid]["status"] == Status.CANDIDATE
        assert rows[pid]["score"] is None
        # It did not compete, so it is not aged by the failure either.
        assert rows[pid]["times_competed"] == 0
    assert rows["n0"]["status"] == Status.FEATURED


def test_retry_of_the_day_scores_the_papers_the_failed_attempt_missed(
    db_path, prompt_path, monkeypatch
):
    """The repro from #720: attempt 1's papers call fails, the day is retried."""
    import db as db_mod
    conn = db_mod.connect(db_path)
    for i in range(3):
        _insert_candidate(conn, f"p{i}", section="papers")
    conn.close()
    sent_papers: list[list[str]] = []

    def fails_once(prompt, label):
        sent = json.loads(prompt.split("```json\n")[1].split("\n```")[0])
        if label == Section.PAPERS:
            sent_papers.append([it["id"] for it in sent])
            if len(sent_papers) == 1:
                raise RuntimeError("endpoint returned a truncated reply")
        return _scores_everything(prompt, label)

    with patch("rank.invoke_ranker", side_effect=fails_once):
        _run_main(db_path, prompt_path, monkeypatch)
        _run_main(db_path, prompt_path, monkeypatch)

    assert len(sent_papers) == 2 and len(sent_papers[1]) == 3  # re-sent on the retry
    rows = _rows(db_path)
    assert {rows[p]["score"] for p in ("p0", "p1", "p2")} == {9}
    assert Status.APPENDIX not in {rows[p]["status"] for p in ("p0", "p1", "p2")}


def test_omitted_paper_stays_in_pool_but_omitted_news_goes_to_appendix(
    db_path, prompt_path, monkeypatch
):
    import db as db_mod
    conn = db_mod.connect(db_path)
    _insert_candidate(conn, "p_missed", section="papers")
    _insert_candidate(conn, "n_missed", section="news")
    conn.close()

    with patch("rank.invoke_ranker", return_value=[]):
        _run_main(db_path, prompt_path, monkeypatch)

    rows = _rows(db_path)
    assert rows["p_missed"]["status"] == Status.CANDIDATE
    assert rows["p_missed"]["score"] is None
    assert rows["n_missed"]["status"] == Status.APPENDIX
    assert rows["n_missed"]["score"] == 0


def test_never_scored_paper_still_ages_out_as_expired_unjudged(db_path):
    """Leaving a paper in the pool can't grow it without bound: the existing
    age-out retires a paper that is never scored as expired_unjudged."""
    import db as db_mod
    conn = db_mod.connect(db_path)
    _insert_candidate(conn, "p_old", section="papers")
    moved = db_mod.age_out_papers_pool(
        conn, max_competes=7, now="2026-07-30T00:00:00Z", max_age_days=7)
    status = conn.execute("SELECT status FROM items WHERE id='p_old'").fetchone()[0]
    conn.close()
    assert moved == 1
    assert status == Status.EXPIRED_UNJUDGED


def test_score_candidates_never_seals_an_unscored_paper(monkeypatch):
    """Unit-level: whatever goes unscored, no paper gets a decision from the fallback."""
    import rank as rank_mod

    monkeypatch.setattr(rank_mod, "invoke_ranker", lambda prompt, label: [])
    candidates = {s.value: [{"id": f"{s.value}-1", "title": "t", "url": "https://x.test",
                             "source": "rss:x"}] for s in Section}
    candidates["papers_prescored"] = []

    decisions = rank_mod.score_candidates(candidates, "rubric")

    assert f"{Section.PAPERS.value}-1" not in decisions
    assert {d["section"] for d in decisions.values()} == {Section.NEWS.value, Section.BLOGS.value}
    assert {d["status"] for d in decisions.values()} == {Status.APPENDIX}
