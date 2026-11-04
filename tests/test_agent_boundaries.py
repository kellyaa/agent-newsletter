"""docs/agent-boundaries.md must stay in sync with policies/agent-policy.yaml."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY = yaml.safe_load((REPO_ROOT / "policies" / "agent-policy.yaml").read_text())
DOC = (REPO_ROOT / "docs" / "agent-boundaries.md").read_text()


def test_boundaries_doc_covers_policy_paths():
    for rel in POLICY["paths"]["agent_forbidden"]:
        assert rel in DOC, rel
    assert POLICY["paths"]["never_commit_by_hand_branch"] in DOC
    for name in POLICY["paths"]["secret_files"]:
        assert name in DOC, name


def test_boundaries_doc_covers_pull_request_policy():
    for label in POLICY["pull_requests"]["hold_labels"]:
        assert label in DOC, label
    assert "never merge" in DOC


def test_boundaries_doc_covers_architecture_policy():
    arch = POLICY["architecture"]
    assert arch["llm_calls_only_via"] in DOC
    assert "tests/test_stage_import_boundary.py" in DOC


def test_boundaries_doc_links_resolve():
    for rel in (
        "policies/agent-policy.yaml",
        "AGENTS.md",
        "SECURITY-AI.md",
        "docs/risk-tiers.md",
        "tests/test_stage_import_boundary.py",
        "tests/test_env_template.py",
    ):
        assert (REPO_ROOT / rel).is_file(), rel
