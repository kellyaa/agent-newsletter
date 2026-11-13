"""Boundary behaviour of the prefilter gates in gates.py.

Flipping ``<=`` to ``<`` in passes_recency, ``>`` to ``>=`` on the recency
override, or ``>=`` to ``>`` in collapse_near_dups changed no test result, so
the edges of each window/threshold were unpinned.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

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


# --- Mutation-probe additions: per-family windows, priorities, defaults -------
# Changing any single RECENCY_DAYS / SOURCE_PRIORITY entry, the default
# NEAR_DUP_THRESHOLD, or the title/body separator in keyword_gate left the
# suite green.

EXPECTED_WINDOWS = {
    "arxiv": 7, "rss": 30, "hn": 3, "reddit": 3, "gh": 14, "html": 30,
}


@pytest.mark.parametrize("family,days", sorted(EXPECTED_WINDOWS.items()))
def test_each_family_window_is_pinned_on_both_sides(family, days):
    source = f"{family}:x"
    assert gates.passes_recency(_item(source, timedelta(days=days)), NOW)
    assert not gates.passes_recency(_item(source, timedelta(days=days, seconds=1)), NOW)


def test_recency_windows_table_has_no_unpinned_families():
    assert {k: v for k, v in gates.RECENCY_DAYS.items() if k != "default"} == EXPECTED_WINDOWS


def test_unknown_family_uses_the_default_window_of_three_days():
    assert gates.RECENCY_DAYS["default"] == 3
    assert gates.passes_recency(_item("mystery:x", timedelta(days=3)), NOW)
    assert not gates.passes_recency(_item("mystery:x", timedelta(days=3, seconds=1)), NOW)


def test_source_priority_order_is_arxiv_then_rss_html_then_gh_hn_reddit():
    p = gates.SOURCE_PRIORITY
    assert p["arxiv"] > p["rss"] == p["html"] > p["gh"] > p["hn"] > p["reddit"]


def test_adjacent_priorities_decide_which_duplicate_survives():
    order = ["arxiv", "rss", "gh", "hn", "reddit"]
    for hi, lo in zip(order, order[1:]):
        # Lower-priority listed first so input order cannot explain the winner.
        items = [{"source": f"{lo}:x", "title": "same title"},
                 {"source": f"{hi}:x", "title": "same title"}]
        kept = gates.collapse_near_dups(items)
        assert [i["source"] for i in kept] == [f"{hi}:x"], (hi, lo)
    html_vs_gh = gates.collapse_near_dups([{"source": "gh:x", "title": "t"},
                                           {"source": "html:x", "title": "t"}])
    assert [i["source"] for i in html_vs_gh] == ["html:x"]


def test_unknown_source_family_loses_to_every_known_one():
    kept = gates.collapse_near_dups([{"source": "mystery:x", "title": "t"},
                                     {"source": "reddit:x", "title": "t"}])
    assert [i["source"] for i in kept] == ["reddit:x"]


def _titles(shared, extra):
    base = [f"w{i}" for i in range(shared)]
    return " ".join(base + extra)


def test_default_near_dup_threshold_is_point_eight_five_inclusive():
    assert gates.NEAR_DUP_THRESHOLD == 0.85
    # 17 shared of 20 in the union -> exactly 0.85: collapses.
    a = {"source": "rss:x", "title": _titles(17, ["p", "q"])}
    b = {"source": "hn:x", "title": _titles(17, ["r"])}
    assert gates.jaccard(gates.title_tokens(a["title"]), gates.title_tokens(b["title"])) == 0.85
    assert len(gates.collapse_near_dups([a, b])) == 1


def test_default_near_dup_threshold_keeps_clearly_different_titles():
    # 8 shared of 10 -> 0.8, below the default: both kept.
    a = {"source": "rss:x", "title": _titles(8, ["p"])}
    b = {"source": "hn:x", "title": _titles(8, ["q"])}
    assert len(gates.collapse_near_dups([a, b])) == 2


def test_keyword_gate_does_not_match_across_the_title_body_boundary():
    kw = re.compile(r"agent loop")
    item = {"title": "building an agent", "raw_text": "loop construction"}
    assert not gates.keyword_gate(item, kw)
    assert gates.keyword_gate({"title": "an agent loop", "raw_text": ""}, kw)
    assert gates.keyword_gate({"title": "x", "raw_text": "the agent loop"}, kw)


def test_recency_treats_a_naive_now_as_utc():
    naive_now = NOW.replace(tzinfo=None)
    days = gates.RECENCY_DAYS["hn"]
    assert gates.passes_recency(_item(age=timedelta(days=days)), naive_now)
    assert not gates.passes_recency(_item(age=timedelta(days=days, seconds=1)), naive_now)
