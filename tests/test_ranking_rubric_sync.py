"""Keep the ranking thresholds and caps consistent across code, prompt and spec.

rank.SECTION_RULES is what actually decides featured / appendix / dropped and
how many items survive per section.  SPEC.md (the "Ranking rubric" table and the
per-section caps list) and prompts/rank.md (axis maxima) describe the same
numbers to humans and to the LLM.  If they drift apart nothing fails at runtime;
the docs just quietly lie.
"""
import re
from pathlib import Path

import pytest

import prompts
from rank import SECTION_RULES

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = (REPO_ROOT / "SPEC.md").read_text()
RANK_PROMPT = prompts.render("rank")  # as the ranker sees it: template + edition fragments

SECTIONS = ("papers", "news", "blogs")


def _threshold_row(section: str) -> list[str]:
    """Cells of the SPEC threshold-table row for *section*."""
    for line in SPEC.splitlines():
        if line.startswith("|") and re.match(
            rf"\|\s*\*\*{section}\*\*\s*\|", line, re.IGNORECASE
        ):
            return [c.strip() for c in line.strip().strip("|").split("|")]
    raise AssertionError(f"no threshold-table row for {section!r} in SPEC.md")


def test_section_rules_cover_exactly_the_three_sections():
    assert set(SECTION_RULES) == set(SECTIONS)


@pytest.mark.parametrize("section", SECTIONS)
def test_spec_thresholds_match_section_rules(section):
    _, featured, appendix, drop, _ = _threshold_row(section)
    rules = SECTION_RULES[section]

    assert int(re.search(r"\d+", featured).group()) == rules["featured_min"]
    lo, hi = (int(n) for n in re.findall(r"\d+", appendix))
    assert lo == rules["appendix_min"]
    assert hi == rules["featured_min"] - 1, "appendix range must end where featured begins"
    assert int(re.search(r"\d+", drop).group()) == rules["appendix_min"]


@pytest.mark.parametrize(
    "section,label",
    [("papers", "Papers"), ("news", "News"), ("blogs", "Blogs")],
)
def test_spec_default_caps_match_section_rules(section, label):
    m = re.search(rf"^- {label}: max (\d+) featured", SPEC, re.MULTILINE)
    assert m, f"SPEC.md no longer lists a cap for {label}"
    assert int(m.group(1)) == SECTION_RULES[section]["cap"]


def test_spec_papers_burst_cap_matches_section_rules():
    rules = SECTION_RULES["papers"]
    m = re.search(r"\*\*burst cap of (\d+)\*\*", SPEC)
    assert m, "SPEC.md no longer documents the papers burst cap"
    assert int(m.group(1)) == rules["burst_cap"]
    assert rules["burst_cap"] > rules["cap"]


def test_spec_burst_trigger_matches_section_rules():
    rules = SECTION_RULES["papers"]
    # "when today's count of score-10 papers reaches 10"
    assert rules["burst_trigger_score"] == 10
    assert "count of score-10 papers reaches 10" in SPEC
    assert rules["burst_trigger_count"] == 10
    assert "burst_trigger_count=10" in SPEC


def test_only_papers_have_burst_settings():
    for section in ("news", "blogs"):
        assert not any(k.startswith("burst") for k in SECTION_RULES[section])


def _axis_maxima(text: str) -> list[int]:
    """Axis maxima from bullets like '- **Relevance ... (0–4).**' / '(0-4)'."""
    return [
        int(m)
        for m in re.findall(r"^- \*\*[^\n]*?\(0[–-](\d+)\)\.?\*\*", text, re.MULTILINE)
    ]


def test_rank_prompt_and_spec_agree_on_axis_maxima():
    prompt_axes = _axis_maxima(RANK_PROMPT)
    spec_axes = _axis_maxima(SPEC)
    assert prompt_axes == [4, 3, 2, 1]
    assert spec_axes == prompt_axes


def test_axis_maxima_sum_to_the_top_of_the_score_range():
    # persist() / the prompt both treat 10 as the maximum score.
    assert sum(_axis_maxima(RANK_PROMPT)) == 10
    assert "range 0–10" in RANK_PROMPT
    assert "integer 0-10" in RANK_PROMPT


@pytest.mark.parametrize("section", SECTIONS)
def test_every_threshold_is_reachable_within_the_score_range(section):
    rules = SECTION_RULES[section]
    assert 0 < rules["appendix_min"] < rules["featured_min"] <= 10
