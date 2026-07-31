"""Tests for rank.py invoke_ranker() error handling."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


class TestInvokeRanker:
    def test_returns_rankings_on_success(self):
        from rank import invoke_ranker
        rankings = [{"id": "x", "score": 8, "tags": [], "why": "good"}]
        with patch("rank.call_llm", return_value={"rankings": rankings}):
            result = invoke_ranker("prompt text", label="papers")
        assert result == rankings

    def test_empty_rankings_list_is_valid(self):
        from rank import invoke_ranker
        with patch("rank.call_llm", return_value={"rankings": []}):
            result = invoke_ranker("prompt", label="blogs")
        assert result == []

    def test_raises_when_rankings_not_a_list(self, tmp_path):
        from rank import invoke_ranker
        import rank as rank_mod
        orig = rank_mod.REPO_ROOT
        rank_mod.REPO_ROOT = tmp_path
        try:
            with patch("rank.call_llm", return_value={"rankings": "not a list"}):
                with pytest.raises(RuntimeError, match="ranker returned no rankings"):
                    invoke_ranker("prompt", label="papers")
        finally:
            rank_mod.REPO_ROOT = orig

    def test_raises_when_rankings_key_missing(self, tmp_path):
        from rank import invoke_ranker
        import rank as rank_mod
        orig = rank_mod.REPO_ROOT
        rank_mod.REPO_ROOT = tmp_path
        try:
            with patch("rank.call_llm", return_value={"other": "data"}):
                with pytest.raises(RuntimeError, match="ranker returned no rankings"):
                    invoke_ranker("prompt", label="news")
        finally:
            rank_mod.REPO_ROOT = orig

    def test_debug_file_written_on_error(self, tmp_path):
        from rank import invoke_ranker
        import rank as rank_mod
        orig = rank_mod.REPO_ROOT
        rank_mod.REPO_ROOT = tmp_path
        bad_response = {"unexpected": "format", "rankings": 99}
        try:
            with patch("rank.call_llm", return_value=bad_response):
                with pytest.raises(RuntimeError):
                    invoke_ranker("prompt", label="test-section")
        finally:
            rank_mod.REPO_ROOT = orig
        debug_files = list((tmp_path / "logs").glob("ranker-output-*.json"))
        assert len(debug_files) == 1
        assert json.loads(debug_files[0].read_text()) == bad_response


class TestRankSectionContentFilter:
    """rank_section() isolates items the endpoint's content filter blocks.

    One blocked item (e.g. a malware writeup) must not cost the whole section.
    """

    @staticmethod
    def _items(n: int) -> list[dict]:
        return [{"id": f"i{k}", "title": f"t{k}"} for k in range(n)]

    def test_passes_through_when_nothing_blocked(self):
        from rank import rank_section
        rankings = [{"id": "i0", "score": 7, "tags": [], "why": "ok"}]
        with patch("rank.invoke_ranker", return_value=rankings) as m:
            out = rank_section("blogs", self._items(3), "rubric")
        assert out == rankings
        assert m.call_count == 1  # no bisection on the happy path

    def test_single_blocked_item_is_dropped(self):
        from llm import ContentFilterError
        from rank import rank_section
        with patch("rank.invoke_ranker", side_effect=ContentFilterError("blocked")):
            out = rank_section("blogs", self._items(1), "rubric")
        assert out == []

    def test_bisects_and_keeps_the_good_items(self):
        from llm import ContentFilterError
        from rank import rank_section

        # 4 items; only "t2" trips the filter. Any batch containing it blocks.
        def fake_invoke(prompt: str, label: str):
            if "t2" in prompt:
                raise ContentFilterError("blocked")
            ids = [f"i{k}" for k in range(4) if f"t{k}" in prompt]
            return [{"id": i, "score": 6, "tags": [], "why": "ok"} for i in ids]

        with patch("rank.invoke_ranker", side_effect=fake_invoke):
            out = rank_section("blogs", self._items(4), "rubric")

        got = sorted(r["id"] for r in out)
        assert got == ["i0", "i1", "i3"]  # the other three survive
