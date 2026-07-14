"""Tests for prefilter.py internals: _prerank_score, dedup helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prefilter import (
    _prerank_score,
    _normalize_title,
    _title_tokens,
    _jaccard,
    _passes_recency,
)


# ---------------------------------------------------------------------------
# _normalize_title
# ---------------------------------------------------------------------------

class TestNormalizeTitle:
    def test_lowercases(self):
        assert _normalize_title("Hello World") == "hello world"

    def test_strips_punctuation(self):
        assert _normalize_title("Hello, World!") == "hello world"

    def test_strips_leading_trailing_whitespace(self):
        assert _normalize_title("  hello  ") == "hello"

    def test_preserves_alphanumeric(self):
        assert _normalize_title("Agent2025") == "agent2025"

    def test_empty_string(self):
        assert _normalize_title("") == ""


# ---------------------------------------------------------------------------
# _title_tokens
# ---------------------------------------------------------------------------

class TestTitleTokens:
    def test_splits_into_words(self):
        assert _title_tokens("hello world") == {"hello", "world"}

    def test_normalizes_before_split(self):
        tokens = _title_tokens("OpenAI Releases Agent SDK!")
        assert "openai" in tokens
        assert "releases" in tokens

    def test_empty_title_returns_empty_set(self):
        assert _title_tokens("") == set()

    def test_single_word(self):
        assert _title_tokens("agents") == {"agents"}


# ---------------------------------------------------------------------------
# _jaccard
# ---------------------------------------------------------------------------

class TestJaccard:
    def test_identical_sets_return_one(self):
        a = {"a", "b", "c"}
        assert _jaccard(a, a) == 1.0

    def test_disjoint_sets_return_zero(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial_overlap(self):
        # intersection=1, union=3
        assert abs(_jaccard({"a", "b"}, {"b", "c"}) - 1/3) < 1e-9

    def test_empty_set_returns_zero(self):
        assert _jaccard(set(), {"a", "b"}) == 0.0
        assert _jaccard({"a"}, set()) == 0.0


# ---------------------------------------------------------------------------
# _prerank_score
# ---------------------------------------------------------------------------

class TestPrerankScore:
    def _item(self, pub_offset_days=0, title="agent llm", raw_text=""):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        pub = (now - timedelta(days=pub_offset_days)).isoformat()
        return {"published_at": pub, "fetched_at": pub, "title": title, "raw_text": raw_text}

    def test_recent_item_scores_higher_than_old(self):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        recent = self._item(pub_offset_days=0)
        old = self._item(pub_offset_days=6)
        assert _prerank_score(recent, now) > _prerank_score(old, now)

    def test_keyword_rich_scores_higher_than_poor(self):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        # Same age; more keyword hits → higher score
        rich = self._item(pub_offset_days=0, title="agent llm workflow planning", raw_text="evals rag")
        poor = self._item(pub_offset_days=0, title="database tuning", raw_text="index rebuild")
        assert _prerank_score(rich, now) > _prerank_score(poor, now)

    def test_score_is_positive(self):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        item = self._item()
        assert _prerank_score(item, now) > 0.0

    def test_score_bounded_above(self):
        """Score = recency * (0.5 + kw); recency ≤ 1, kw ≤ 1 → max = 1.5."""
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        # Just-published item with max keyword hits
        item = {"published_at": now.isoformat(), "fetched_at": now.isoformat(),
                 "title": "agent llm workflow planning evals",
                 "raw_text": "rag multi-agent code generation"}
        score = _prerank_score(item, now)
        assert score <= 1.5

    def test_unparseable_published_at_defaults_to_zero_age(self):
        now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
        item = {"published_at": "not-a-date", "fetched_at": "not-a-date",
                "title": "agent", "raw_text": ""}
        # Should not raise; defaults age_days=0.0
        score = _prerank_score(item, now)
        assert score > 0.0
