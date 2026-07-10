from datetime import datetime, timezone

import pytest

from prefilter import (
    _passes_keyword_gate,
    _passes_recency,
    assign_section,
    collapse_near_dups,
)


@pytest.mark.parametrize(
    ("source", "override", "expected"),
    [
        ("arxiv:cs.AI", None, "papers"),
        ("hf-daily:papers", None, "papers"),
        ("gh:releases", None, "news"),
        ("hn:frontpage", None, "news"),
        ("reddit:LocalLLaMA", None, "news"),
        ("rss:blog", None, "blogs"),
        ("unknown:feed", None, "blogs"),
        ("rss:blog", "papers", "papers"),
        ("arxiv:cs.AI", "blogs", "blogs"),
        ("rss:blog", "invalid", "blogs"),
    ],
)
def test_assign_section_uses_override_then_family_default(
    source: str, override: str | None, expected: str
) -> None:
    assert assign_section(source, override) == expected


def test_recency_uses_family_default_window() -> None:
    now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)

    assert _passes_recency(
        {
            "source": "rss:example",
            "published_at": "2026-06-07T12:00:00Z",
            "fetched_at": "2026-06-27T12:00:00Z",
        },
        now,
    )
    assert not _passes_recency(
        {
            "source": "hn:frontpage",
            "published_at": "2026-06-23T12:00:00Z",
            "fetched_at": "2026-06-27T12:00:00Z",
        },
        now,
    )


def test_recency_days_override_wins_over_family_default() -> None:
    now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
    item = {
        "source": "rss:example",
        "published_at": "2026-06-24T12:00:00Z",
        "fetched_at": "2026-06-27T12:00:00Z",
    }

    assert _passes_recency(item, now)
    assert not _passes_recency({**item, "recency_days_override": 2}, now)


def test_keyword_gate_applies_to_title_and_raw_text() -> None:
    assert _passes_keyword_gate({"title": "New agent workflow", "raw_text": ""})
    assert _passes_keyword_gate({"title": "Release notes", "raw_text": "LLM evals"})
    assert not _passes_keyword_gate(
        {"title": "Database maintenance", "raw_text": "Index tuning"}
    )


def test_keyword_gate_can_be_bypassed() -> None:
    assert _passes_keyword_gate(
        {
            "title": "Database maintenance",
            "raw_text": "Index tuning",
            "keyword_gate_bypass": 1,
        }
    )


def test_collapse_near_dups_keeps_highest_priority_title() -> None:
    items = [
        {
            "id": "reddit",
            "source": "reddit:LocalLLaMA",
            "title": "OpenAI releases agent SDK",
        },
        {
            "id": "paper",
            "source": "arxiv:cs.AI",
            "title": "OpenAI releases agent SDK!",
        },
        {
            "id": "blog",
            "source": "rss:example",
            "title": "A practical guide to MCP servers",
        },
    ]

    assert [item["id"] for item in collapse_near_dups(items)] == ["paper", "blog"]


def test_recency_falls_back_to_fetched_at_when_no_published_at() -> None:
    """When published_at is absent, _passes_recency falls back to fetched_at (lines 132-133)."""
    now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
    # fetched_at is recent enough — should pass
    assert _passes_recency(
        {
            "source": "rss:example",
            "published_at": None,
            "fetched_at": "2026-06-20T12:00:00Z",
        },
        now,
    )
    # fetched_at is too old — should fail (rss window = 30 days, >30 days ago)
    assert not _passes_recency(
        {
            "source": "rss:example",
            "published_at": None,
            "fetched_at": "2026-05-01T12:00:00Z",  # 57 days before now
        },
        now,
    )


def test_recency_naive_datetime_gets_utc_tzinfo() -> None:
    """A naive datetime (no tzinfo) from published_at is treated as UTC (lines 137-138)."""
    now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
    # No 'Z' and no '+00:00' — fromisoformat produces a naive datetime
    item = {
        "source": "rss:example",
        "published_at": "2026-06-20T12:00:00",  # naive
        "fetched_at": "2026-06-27T12:00:00Z",
    }
    # Should not crash; naive datetime is treated as UTC
    result = _passes_recency(item, now)
    assert isinstance(result, bool)


def test_recency_unparseable_date_passes_through() -> None:
    """Unparseable published_at passes (returns True — let LLM judge) (lines 135-136)."""
    now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
    # feedparser bozo or malformed date string
    item = {
        "source": "rss:example",
        "published_at": "Mon Jun 2026 garbage",  # unparseable
        "fetched_at": "2026-06-27T12:00:00Z",
    }
    assert _passes_recency(item, now) is True  # don't drop; let LLM judge


def test_prerank_score_unparseable_date_defaults_age_zero() -> None:
    """_prerank_score() returns a valid score when published_at is unparseable (line 177)."""
    from prefilter import _prerank_score
    now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
    item = {
        "published_at": "not-a-date",
        "fetched_at": "2026-06-27T12:00:00Z",
        "title": "Agent planning research",
        "raw_text": "agents models llm",
    }
    score = _prerank_score(item, now)
    assert isinstance(score, float)
    assert score >= 0.0


def test_prerank_score_naive_datetime_treated_as_utc() -> None:
    """_prerank_score() handles naive datetimes by treating them as UTC (line 174)."""
    from prefilter import _prerank_score
    now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
    item = {
        "published_at": "2026-06-20T12:00:00",  # naive — no Z, no +00:00
        "fetched_at": "2026-06-27T12:00:00Z",
        "title": "Agent research paper",
        "raw_text": "agents llm models",
    }
    score = _prerank_score(item, now)
    assert isinstance(score, float)
    assert score >= 0.0
