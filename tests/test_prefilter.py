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
