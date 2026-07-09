"""Tests for rank.py: assign_statuses, effective_cap, build_prompt."""
from __future__ import annotations

import json
import pytest

from rank import assign_statuses, effective_cap, build_prompt, SECTION_RULES


# ---------------------------------------------------------------------------
# effective_cap
# ---------------------------------------------------------------------------

class TestEffectiveCap:
    """effective_cap() picks the static cap for news/blogs, burst for papers."""

    def test_news_always_returns_static_cap(self):
        items = [{"score": 10}] * 20  # lots of high scores
        assert effective_cap("news", items) == SECTION_RULES["news"]["cap"]

    def test_blogs_always_returns_static_cap(self):
        items = [{"score": 9}] * 20
        assert effective_cap("blogs", items) == SECTION_RULES["blogs"]["cap"]

    def test_papers_static_cap_when_burst_not_triggered(self):
        rules = SECTION_RULES["papers"]
        # Fewer than burst_trigger_count items at burst_trigger_score
        items = [{"score": rules["burst_trigger_score"]}] * (rules["burst_trigger_count"] - 1)
        assert effective_cap("papers", items) == rules["cap"]

    def test_papers_burst_cap_when_threshold_exactly_met(self):
        rules = SECTION_RULES["papers"]
        # Exactly burst_trigger_count items at burst_trigger_score
        items = [{"score": rules["burst_trigger_score"]}] * rules["burst_trigger_count"]
        assert effective_cap("papers", items) == rules["burst_cap"]

    def test_papers_burst_cap_when_threshold_exceeded(self):
        rules = SECTION_RULES["papers"]
        items = [{"score": rules["burst_trigger_score"]}] * (rules["burst_trigger_count"] + 5)
        assert effective_cap("papers", items) == rules["burst_cap"]

    def test_papers_burst_counts_only_at_or_above_trigger_score(self):
        rules = SECTION_RULES["papers"]
        # Some items just below trigger score should NOT count
        below = [{"score": rules["burst_trigger_score"] - 1}] * 20
        assert effective_cap("papers", below) == rules["cap"]

    def test_papers_mixed_burst_triggers_on_high_count(self):
        rules = SECTION_RULES["papers"]
        high = [{"score": rules["burst_trigger_score"]}] * rules["burst_trigger_count"]
        low = [{"score": 1}] * 10
        assert effective_cap("papers", high + low) == rules["burst_cap"]


# ---------------------------------------------------------------------------
# assign_statuses
# ---------------------------------------------------------------------------

def _make_items(section: str, scores: list[int]) -> list[dict]:
    return [
        {"id": f"{section}-{i}", "score": s, "tags": [], "why": f"item {i}"}
        for i, s in enumerate(scores)
    ]


class TestAssignStatusesPapers:
    """Papers-specific behaviour: pool keep, featured cap, appendix, dropped."""

    def test_top_items_get_featured_up_to_cap(self):
        rules = SECTION_RULES["papers"]
        cap = rules["cap"]
        # All items above featured_min; enough to fill the cap
        items = _make_items("papers", [rules["featured_min"]] * (cap + 3))
        result = assign_statuses({"papers": items})
        featured = sum(1 for d in result.values() if d["status"] == "featured")
        assert featured == cap

    def test_papers_above_bar_but_lost_cap_stay_candidate(self):
        rules = SECTION_RULES["papers"]
        cap = rules["cap"]
        # More items above featured_min than cap → extras stay 'candidate'
        n_above = cap + 4
        items = _make_items("papers", [rules["featured_min"]] * n_above)
        result = assign_statuses({"papers": items})
        candidate = sum(1 for d in result.values() if d["status"] == "candidate")
        assert candidate == n_above - cap

    def test_mid_band_papers_go_to_appendix(self):
        rules = SECTION_RULES["papers"]
        items = _make_items(
            "papers",
            [rules["appendix_min"], rules["appendix_min"] + 1],
        )
        result = assign_statuses({"papers": items})
        for d in result.values():
            assert d["status"] == "appendix"

    def test_below_appendix_min_gets_dropped(self):
        rules = SECTION_RULES["papers"]
        items = _make_items("papers", [rules["appendix_min"] - 1, 0])
        result = assign_statuses({"papers": items})
        for d in result.values():
            assert d["status"] == "dropped"

    def test_result_contains_score_tags_why_section(self):
        rules = SECTION_RULES["papers"]
        items = [{"id": "x", "score": rules["featured_min"], "tags": ["evals"], "why": "good"}]
        result = assign_statuses({"papers": items})
        d = result["x"]
        assert d["score"] == rules["featured_min"]
        assert d["tags"] == ["evals"]
        assert d["why"] == "good"
        assert d["section"] == "papers"


class TestAssignStatusesNewsBlogs:
    """News/blogs: cap overflow → appendix (no pool keep)."""

    @pytest.mark.parametrize("section", ["news", "blogs"])
    def test_featured_fills_cap(self, section):
        rules = SECTION_RULES[section]
        items = _make_items(section, [rules["featured_min"]] * (rules["cap"] + 2))
        result = assign_statuses({section: items})
        featured = sum(1 for d in result.values() if d["status"] == "featured")
        assert featured == rules["cap"]

    @pytest.mark.parametrize("section", ["news", "blogs"])
    def test_cap_overflow_goes_to_appendix_not_candidate(self, section):
        rules = SECTION_RULES[section]
        n_above = rules["cap"] + 3
        items = _make_items(section, [rules["featured_min"]] * n_above)
        result = assign_statuses({section: items})
        # Overflow items should be appendix, NOT candidate
        statuses = [d["status"] for d in result.values()]
        assert "candidate" not in statuses
        appendix = sum(1 for s in statuses if s == "appendix")
        assert appendix == n_above - rules["cap"]

    @pytest.mark.parametrize("section", ["news", "blogs"])
    def test_mid_band_appendix(self, section):
        rules = SECTION_RULES[section]
        items = _make_items(section, [rules["appendix_min"]])
        result = assign_statuses({section: items})
        assert list(result.values())[0]["status"] == "appendix"

    @pytest.mark.parametrize("section", ["news", "blogs"])
    def test_below_min_dropped(self, section):
        rules = SECTION_RULES[section]
        items = _make_items(section, [rules["appendix_min"] - 1])
        result = assign_statuses({section: items})
        assert list(result.values())[0]["status"] == "dropped"


class TestAssignStatusesMultiSection:
    """assign_statuses handles multiple sections in one call."""

    def test_multiple_sections_independent(self):
        rules_p = SECTION_RULES["papers"]
        rules_n = SECTION_RULES["news"]
        papers = _make_items("papers", [rules_p["featured_min"]] * 3)
        news = _make_items("news", [rules_n["featured_min"]] * 3)
        result = assign_statuses({"papers": papers, "news": news})
        paper_ids = {it["id"] for it in papers}
        news_ids = {it["id"] for it in news}
        for iid, d in result.items():
            if iid in paper_ids:
                assert d["section"] == "papers"
            elif iid in news_ids:
                assert d["section"] == "news"

    def test_empty_section_produces_no_output(self):
        result = assign_statuses({"papers": [], "news": [], "blogs": []})
        assert result == {}


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    """build_prompt() assembles a prompt string with section and item count."""

    def test_contains_section_name(self):
        prompt = build_prompt("papers", [], "rubric text")
        assert "papers" in prompt

    def test_contains_item_count(self):
        items = [{"id": "a"}, {"id": "b"}]
        prompt = build_prompt("papers", items, "rubric")
        assert "2" in prompt

    def test_rubric_included(self):
        rubric = "RANK BY NOVELTY"
        prompt = build_prompt("news", [], rubric)
        assert rubric in prompt

    def test_items_serialized_as_json(self):
        items = [{"id": "abc-123", "title": "Test paper"}]
        prompt = build_prompt("papers", items, "rubric")
        assert "abc-123" in prompt
        assert "Test paper" in prompt

    def test_zero_items(self):
        prompt = build_prompt("blogs", [], "rubric")
        assert "0" in prompt
