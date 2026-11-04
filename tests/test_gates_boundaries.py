"""Boundary behaviour of the prefilter gates in gates.py.

Flipping ``<=`` to ``<`` in passes_recency, ``>`` to ``>=`` on the recency
override, or ``>=`` to ``>`` in collapse_near_dups changed no test result, so
the edges of each window/threshold were unpinned.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import gates

NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


def _item(source="hn:front", age: timedelta = timedelta(0), **extra):
    pub = (NOW - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": "x", "source": source, "title": "t", "raw_text": "",
            "published_at": pub, "fetched_at": pub, **extra}


def test_recency_window_is_inclusive_at_exactly_the_limit():
    days = gates.RECENCY_DAYS["hn"]
    assert gates.passes_recency(_item(age=timedelta(days=days)), NOW)


def test_recency_rejects_one_second_past_the_limit():
    days = gates.RECENCY_DAYS["hn"]
    assert not gates.passes_recency(_item(age=timedelta(days=days, seconds=1)), NOW)


def test_recency_override_replaces_the_family_window():
    # hn default is 3 days; a 10-day override keeps a 5-day-old item.
    assert gates.passes_recency(_item(age=timedelta(days=5), recency_days_override=10), NOW)
    assert not gates.passes_recency(_item(age=timedelta(days=11), recency_days_override=10), NOW)


def test_zero_override_is_ignored_and_family_window_applies():
    # 0 is not a valid window; the family default (hn: 3 days) must apply.
    item = _item(age=timedelta(days=1), recency_days_override=0)
    assert gates.passes_recency(item, NOW)


def test_one_day_override_is_honoured():
    assert gates.passes_recency(_item(source="rss:x", age=timedelta(hours=23), recency_days_override=1), NOW)
    assert not gates.passes_recency(_item(source="rss:x", age=timedelta(days=2), recency_days_override=1), NOW)


def test_near_dup_collapses_titles_exactly_at_the_threshold():
    # Token sets {a,b,c,d} vs {a,b,c,e}: jaccard = 3/5 = 0.6.
    a = {"source": "rss:x", "title": "a b c d"}
    b = {"source": "hn:x", "title": "a b c e"}
    assert gates.jaccard(gates.title_tokens(a["title"]), gates.title_tokens(b["title"])) == 0.6
    assert [i["title"] for i in gates.collapse_near_dups([a, b], threshold=0.6)] == ["a b c d"]


def test_near_dup_keeps_titles_just_below_the_threshold():
    a = {"source": "rss:x", "title": "a b c d"}
    b = {"source": "hn:x", "title": "a b c e"}
    kept = gates.collapse_near_dups([a, b], threshold=0.61)
    assert sorted(i["title"] for i in kept) == ["a b c d", "a b c e"]
