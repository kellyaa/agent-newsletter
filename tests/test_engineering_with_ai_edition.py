"""The shipped second edition, ``engineering-with-ai`` (#304, renamed in #323), lints clean and keeps
the promises the multi-edition design (#291) made about it."""
from __future__ import annotations

import re

import pytest

import check_edition
import edition
import prompts

SLUG = "engineering-with-ai"

# #291 section 10: the draft tag vocabulary.
EXPECTED_TAGS = {
    "ai-coding", "code-review", "testing", "security", "supply-chain",
    "architecture", "devex", "platform", "productivity", "team-process",
    "llmops", "tooling", "research",
}


@pytest.fixture(scope="module")
def ed():
    return edition.load(SLUG)


def test_lints_clean_with_no_todo_placeholders_left():
    result = check_edition.lint(SLUG)
    assert result.errors == []
    assert result.warnings == []


def test_ships_as_a_draft_that_runs_after_agents(ed):
    assert ed.status == "draft"
    assert ed.order == 20 > edition.load("agents").order
    assert ed.name == "Engineering with AI Daily"


def test_discovered_in_run_order_with_agents_first():
    found = [slug for slug, ed, err in edition.discover() if err is None]
    assert found.index("agents") < found.index(SLUG)


def test_tags_are_the_ones_proposed_in_the_design(ed):
    assert set(ed.tags) == EXPECTED_TAGS


def test_both_prompts_render_with_this_editions_subject(ed):
    for stage in prompts.STAGES:
        text = prompts.render(stage, ed)
        assert "Engineering with AI" in text
        assert "{{" not in text


def test_sources_cover_every_family_in_the_design(ed):
    sources = check_edition.lint(SLUG).sources
    assert {s["id"] for s in sources["arxiv"]} and all("cat:cs.SE" in s["query"] for s in sources["arxiv"])
    assert {s["id"] for s in sources["reddit"]} == {
        "reddit-experienceddevs", "reddit-programming", "reddit-softwareengineering",
    }
    hn_queries = {s["query"] for s in sources["hn"]}
    assert hn_queries == {
        "claude code", "ai coding", "code review", "coding agent", "llm code", "agents", "ai",
    }
    assert len(sources["rss"]) >= 8


# Narrow coding terms where the first-hand long tail sits under 40 points.
LOW_FLOOR_HN_QUERIES = {"claude code", "ai coding", "coding agent"}


def test_hn_sources_use_a_week_window_and_a_points_floor(ed):
    # #330: 48h windows returned nothing for these narrow queries. The window is
    # the lever; recency_days must match or the prefilter (hn default 3d) drops them.
    for s in check_edition.lint(SLUG).sources["hn"]:
        assert s["hours_back"] == 168
        assert s["min_points"] >= (10 if s["query"] in LOW_FLOOR_HN_QUERIES else 40)
        assert s["recency_days"] >= 7


def test_hn_sources_skip_product_launches(ed):
    # Show HN / Launch HN were most of the edition's HN items, and almost none
    # were first-hand practice.
    assert all(s["exclude_launches"] is True for s in check_edition.lint(SLUG).sources["hn"])


def test_broad_hn_queries_keep_a_high_points_floor(ed):
    # `ai` and `agents` match general AI news; only the floor keeps them sane.
    by_query = {s["query"]: s for s in check_edition.lint(SLUG).sources["hn"]}
    assert by_query["ai"]["min_points"] >= 100
    assert by_query["agents"]["min_points"] >= 40


def test_keyword_gate_admits_teaser_length_practitioner_titles(ed):
    # Real titles the old gate dropped on teaser-length RSS summaries.
    gate = re.compile("|".join(ed.keyword_regexes()), re.IGNORECASE)
    for title in (
        "Engineers lose 17+ days a year to AI rework",
        "Agents still need design reviews.",
        "Claude Mods: Flipping the Harness Inside Out",
        "I Took a Look at A Completely Vibe-Coded App",
    ):
        assert gate.search(title), title
    assert not gate.search("Long live the engineering manager")


def test_reddit_is_noisy_so_it_keeps_the_keyword_gate_and_a_small_limit(ed):
    for s in check_edition.lint(SLUG).sources["reddit"]:
        assert not s.get("keyword_gate_bypass")
        assert s["limit"] <= 5
