"""The shipped second edition, ``engineering-with-ai`` (#304, renamed in #323), lints clean and keeps
the promises the multi-edition design (#291) made about it."""
from __future__ import annotations

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
    assert hn_queries == {"claude code", "ai coding", "code review", "coding agent", "llm code"}
    assert len(sources["rss"]) >= 8


def test_hn_sources_use_a_week_window_and_a_points_floor(ed):
    # #330: 48h windows returned nothing for these narrow queries. The window is
    # the lever; recency_days must match or the prefilter (hn default 3d) drops them.
    for s in check_edition.lint(SLUG).sources["hn"]:
        assert s["hours_back"] == 168
        assert s["min_points"] >= 40
        assert s["recency_days"] >= 7


def test_reddit_is_noisy_so_it_keeps_the_keyword_gate_and_a_high_floor(ed):
    for s in check_edition.lint(SLUG).sources["reddit"]:
        assert not s.get("keyword_gate_bypass")
        assert s["min_score"] >= 100
