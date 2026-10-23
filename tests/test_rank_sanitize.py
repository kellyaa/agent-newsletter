"""rank.sanitize_rankings: malformed LLM entries must not crash or distort ranking.

The ranker's JSON schema is only enforced by providers that honour
response_format=json_schema; with json_object/none a bad entry reaches
assign_statuses, where a non-int score crashes the sort (killing the rank
stage) and an unknown id wins a featured slot against the cap.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from conftest import base_template

import rank


def _e(id_="a", score=8, tags=("evals",), why="ok", **kw):
    return {"id": id_, "score": score, "tags": list(tags) if tags is not None else None,
            "why": why, **kw}


class TestSanitizeRankings:
    def test_valid_entries_pass_through(self):
        out = rank.sanitize_rankings([_e("a", 8), _e("b", 0)], {"a", "b"}, "papers")
        assert [e["id"] for e in out] == ["a", "b"]
        assert [e["score"] for e in out] == [8, 0]

    @pytest.mark.parametrize("bad", ["8", None, 11, -1, 7.5, True, [8]])
    def test_bad_score_dropped(self, bad):
        assert rank.sanitize_rankings([_e("a", bad)], {"a"}, "papers") == []

    def test_missing_score_dropped(self):
        assert rank.sanitize_rankings([{"id": "a", "tags": [], "why": "x"}], {"a"}, "p") == []

    def test_integral_float_score_coerced(self):
        out = rank.sanitize_rankings([_e("a", 9.0)], {"a"}, "papers")
        assert out[0]["score"] == 9 and isinstance(out[0]["score"], int)

    def test_unknown_id_dropped(self):
        assert rank.sanitize_rankings([_e("zzz", 9)], {"a"}, "papers") == []

    @pytest.mark.parametrize("bad_id", [None, 1, ["a"]])
    def test_non_string_id_dropped(self, bad_id):
        assert rank.sanitize_rankings([_e(bad_id, 9)], {"a"}, "papers") == []

    def test_duplicate_id_keeps_first(self):
        out = rank.sanitize_rankings([_e("a", 9), _e("a", 2)], {"a"}, "papers")
        assert len(out) == 1 and out[0]["score"] == 9

    def test_non_dict_entry_dropped(self):
        out = rank.sanitize_rankings(["a", None, _e("b", 7)], {"a", "b"}, "papers")
        assert [e["id"] for e in out] == ["b"]

    def test_null_tags_and_why_normalised(self):
        out = rank.sanitize_rankings([_e("a", 8, tags=None, why=None)], {"a"}, "papers")
        assert out[0]["tags"] == [] and out[0]["why"] == ""

    def test_non_string_tags_filtered(self):
        e = _e("a", 8)
        e["tags"] = ["evals", 3, None]
        out = rank.sanitize_rankings([e], {"a"}, "papers")
        assert out[0]["tags"] == ["evals"]

    def test_warns_when_dropping(self, caplog):
        with caplog.at_level("WARNING", logger="rank"):
            rank.sanitize_rankings([_e("zzz")], {"a"}, "papers")
        assert "ignored 1" in caplog.text


class TestRankSectionUsesSanitizer:
    def test_malformed_entries_do_not_crash_and_item_falls_back(self):
        items = [{"id": "a", "title": "t"}, {"id": "b", "title": "t"}]
        raw = [_e("a", "high"), _e("ghost", 10), _e("b", 9)]
        with patch("rank.invoke_ranker", return_value=raw):
            out = rank.rank_section("papers", items, "rubric")
        assert [e["id"] for e in out] == ["b"]
        # Whole pipeline step doesn't raise, and the ghost id gets no slot.
        decisions = rank.assign_statuses({"papers": out})
        assert set(decisions) == {"b"}


def test_main_ghost_id_does_not_displace_real_items(tmp_path, monkeypatch):
    """End-to-end: unknown id is ignored; omitted real item gets appendix fallback."""
    import candidates as candidates_mod
    import db as db_mod

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    conn = db_mod.connect(db_path)
    for iid in ("a", "b"):
        conn.execute(
            "INSERT INTO items (id, source, url, canonical_url, title, fetched_at, status,"
            " section, first_seen_date, last_seen_date, appearances, times_competed)"
            " VALUES (?, 'rss:x', 'https://a.com/'||?, 'https://a.com/'||?, 'T',"
            " '2026-07-09T00:00:00Z', 'candidate', 'blogs', '2026-07-09', '2026-07-09', 1, 0)",
            (iid, iid, iid),
        )
    conn.commit()
    conn.close()
    prompt = tmp_path / "rank.md"
    prompt.write_text(base_template("rank"))
    monkeypatch.setattr(rank, "PROMPT_PATH", prompt)
    monkeypatch.setattr(rank, "RANKED_PATH", tmp_path / "ranked.json")
    monkeypatch.setattr(rank, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(rank, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(candidates_mod, "connect", lambda: db_mod.connect(db_path))

    raw = [_e("ghost", 10), _e("a", None), _e("b", 9)]
    with patch("rank.invoke_ranker", return_value=raw):
        assert rank.main() == 0

    conn = db_mod.connect(db_path)
    rows = {r["id"]: (r["status"], r["score"]) for r in conn.execute("SELECT id,status,score FROM items")}
    conn.close()
    assert rows == {"a": ("appendix", 0), "b": ("featured", 9)}
