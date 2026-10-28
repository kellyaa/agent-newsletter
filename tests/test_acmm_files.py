"""Governance/learning files that ACMM checks for must exist and stay coherent."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_governance_and_continuity_files_exist_and_are_nonempty():
    for rel in (
        "docs/risk-tiers.md",
        "SECURITY-AI.md",
        ".claude/checkpoint.md",
        ".claude/session-summary.md",
    ):
        assert (REPO_ROOT / rel).read_text().strip(), rel


def test_labeler_rules_are_well_formed():
    rules = yaml.safe_load((REPO_ROOT / ".github" / "labeler.yml").read_text())
    assert rules
    for label, entries in rules.items():
        assert isinstance(label, str) and entries, label
        assert all("changed-files" in e for e in entries), label


def test_security_ai_agrees_with_agent_policy():
    policy = yaml.safe_load((REPO_ROOT / "policies" / "agent-policy.yaml").read_text())
    text = (REPO_ROOT / "SECURITY-AI.md").read_text()
    for rel in policy["paths"]["agent_forbidden"]:
        assert rel in text, rel
    assert policy["paths"]["never_commit_by_hand_branch"] in text
