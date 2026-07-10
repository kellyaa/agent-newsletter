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
