"""The ranker score range (integer 0-10) has one home: ``models.SCORE_MIN`` / ``SCORE_MAX``.

The bounds were spelled in rank's output schema, rank's sanitizer and the
site's zod schema, and ``edition.yaml`` thresholds had no upper bound, so
``featured_min: 70`` loaded cleanly and that section never featured anything.
These tests pin:

* the schema, the sanitizer and the site agree with ``models``;
* every score threshold in ``edition.yaml`` must lie in the range;
* no bare ``10`` bound comes back in ``rank.py``.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

import check_edition
import edition
import rank
from models import SCORE_MAX, SCORE_MIN, SECTION_SCORE_KEYS, Section
from test_check_edition import _clone_agents, editions_dir  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[1]


def test_rank_schema_uses_the_range():
    score = rank.RANKER_OUTPUT_SCHEMA["properties"]["rankings"]["items"]["properties"]["score"]
    assert (score["minimum"], score["maximum"]) == (SCORE_MIN, SCORE_MAX)


def test_sanitizer_accepts_exactly_the_range():
    ids = {"lo", "hi", "under", "over"}
    kept = rank.sanitize_rankings(
        [{"id": "lo", "score": SCORE_MIN}, {"id": "hi", "score": SCORE_MAX},
         {"id": "under", "score": SCORE_MIN - 1}, {"id": "over", "score": SCORE_MAX + 1}],
        ids, Section.PAPERS,
    )
    assert sorted(e["id"] for e in kept) == ["hi", "lo"]


def test_site_zod_score_bounds_match_models():
    text = (REPO / "site" / "src" / "content.config.ts").read_text()
    m = re.search(r"score:\s*z\.number\(\)\.int\(\)\.min\((\d+)\)\.max\((\d+)\)", text)
    assert m, "score schema not found in content.config.ts"
    assert (int(m.group(1)), int(m.group(2))) == (SCORE_MIN, SCORE_MAX)


def test_site_scorebar_default_max_matches_models():
    text = (REPO / "site" / "src" / "components" / "ScoreBar.astro").read_text()
    m = re.search(r"max\s*=\s*(\d+)", text)
    assert m and int(m.group(1)) == SCORE_MAX


def test_score_keys_are_section_keys():
    assert set(SECTION_SCORE_KEYS) <= set(edition._SECTION_REQUIRED) | set(edition._SECTION_BURST)


def test_committed_editions_are_in_range():
    for _slug, ed, err in edition.discover():
        assert err is None, err
        for name, rules in ed.section_rules().items():
            for k in SECTION_SCORE_KEYS:
                if k in rules:
                    assert SCORE_MIN <= rules[k] <= SCORE_MAX, (ed.slug, name, k)


def _lint_with(editions_dir, section: str, **rules: int) -> list[str]:
    dest = _clone_agents(editions_dir)
    data = yaml.safe_load((dest / "edition.yaml").read_text())
    data["sections"][section].update(rules)
    (dest / "edition.yaml").write_text(yaml.safe_dump(data))
    return check_edition.lint("demo").errors


def _range_errors(errors: list[str]) -> list[str]:
    return [e for e in errors if f"must be between {SCORE_MIN} and {SCORE_MAX}" in e]


OVER = SCORE_MAX + 1
BURST = {"burst_cap": 10, "burst_trigger_count": 10}


@pytest.mark.parametrize("rules,key", [
    ({"featured_min": OVER}, "featured_min"),
    ({"featured_min": OVER + 1, "appendix_min": OVER}, "appendix_min"),
    ({**BURST, "burst_trigger_score": OVER}, "burst_trigger_score"),
], ids=["featured_min", "appendix_min", "burst_trigger_score"])
def test_threshold_above_range_is_rejected(editions_dir, rules, key):
    errors = _range_errors(_lint_with(editions_dir, Section.PAPERS, **rules))
    assert any(f"sections.papers.{key} must be between" in e for e in errors), errors


def test_thresholds_at_range_max_load(editions_dir):
    rules = {"featured_min": SCORE_MAX, "appendix_min": SCORE_MAX, **BURST,
             "burst_trigger_score": SCORE_MAX}
    assert _lint_with(editions_dir, Section.PAPERS, **rules) == []


def test_cap_is_not_a_score_key(editions_dir):
    assert _range_errors(_lint_with(editions_dir, Section.NEWS, cap=OVER + 5)) == []


def test_rank_has_no_literal_score_bound():
    tree = ast.parse((REPO / "scripts" / "rank.py").read_text(encoding="utf-8"))
    hits = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Compare)
            and any(isinstance(c, ast.Constant) and c.value == SCORE_MAX for c in [n.left, *n.comparators])]
    hits += [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Dict)
             and any(isinstance(k, ast.Constant) and k.value in {"minimum", "maximum"}
                     and isinstance(v, ast.Constant) and isinstance(v.value, int)
                     for k, v in zip(n.keys, n.values))
             and any(isinstance(k, ast.Constant) and k.value == "maximum" for k in n.keys)
             and any(isinstance(v, ast.Constant) and v.value == SCORE_MAX for v in n.values)]
    assert hits == [], f"rank.py:{hits} use models.SCORE_MIN / SCORE_MAX"
