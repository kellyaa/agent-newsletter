"""Keep .github/auto-qa-tuning.json well-formed and pointed at the real gate."""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TUNING = json.loads((REPO_ROOT / ".github" / "auto-qa-tuning.json").read_text())


def test_tuning_never_lowers_and_needs_review():
    t = TUNING["tuning"]
    assert t["may_lower"] is False
    assert t["requires_human_review"] is True
    assert TUNING["thresholds"]["coverage_percent"]["direction"] == "raise_only"


def test_tuning_numbers_are_positive_ints():
    t = TUNING["tuning"]
    for key in ("observation_window_runs", "raise_when_headroom_points_at_least", "raise_step_points"):
        assert isinstance(t[key], int) and not isinstance(t[key], bool) and t[key] > 0, key


def test_coverage_source_file_carries_the_gate():
    source = REPO_ROOT / TUNING["thresholds"]["coverage_percent"]["source"]
    assert re.search(r"--cov-fail-under=\d+", source.read_text())
