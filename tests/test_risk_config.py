""".claude/risk-config.json must stay well-formed and agree with docs/risk-tiers.md."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((REPO_ROOT / ".claude" / "risk-config.json").read_text())


def test_tiers_are_contiguous_and_complete():
    assert CONFIG["version"] == 1
    assert [t["tier"] for t in CONFIG["tiers"]] == [0, 1, 2, 3]
    for t in CONFIG["tiers"]:
        assert t["name"] and t["review"] and t["paths"], t["tier"]


def test_literal_paths_exist():
    for t in CONFIG["tiers"]:
        for p in t["paths"]:
            if "*" in p:
                continue
            if p == ".env":  # never committed
                continue
            assert (REPO_ROOT / p).exists(), p


def test_documented_paths_appear_in_risk_tiers_doc():
    doc = (REPO_ROOT / "docs" / "risk-tiers.md").read_text()
    for tier in (2, 3):
        for p in CONFIG["tiers"][tier]["paths"]:
            assert p.rstrip("/*") in doc, p


def test_tier3_covers_every_agent_forbidden_path():
    policy = yaml.safe_load((REPO_ROOT / "policies" / "agent-policy.yaml").read_text())
    tier3 = " ".join(CONFIG["tiers"][3]["paths"])
    for rel in policy["paths"]["agent_forbidden"]:
        assert rel in tier3, rel
    assert "policies/agent-policy.yaml" in tier3


def test_agent_pr_rules_match_policy():
    policy = yaml.safe_load((REPO_ROOT / "policies" / "agent-policy.yaml").read_text())
    pr = CONFIG["agent_pull_requests"]
    assert pr["label"] in policy["pull_requests"]["hold_labels"]
    assert pr["agents_may_merge"] is policy["pull_requests"]["agents_may_merge"]
