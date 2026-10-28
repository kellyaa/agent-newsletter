"""policies/agent-policy.yaml must stay well-formed and point at real files."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY = yaml.safe_load((REPO_ROOT / "policies" / "agent-policy.yaml").read_text())


def test_policy_version_and_sections():
    assert POLICY["version"] == 1
    assert set(POLICY) == {"version", "pull_requests", "paths", "testing", "architecture"}


def test_pull_request_policy_never_lets_agents_merge_or_unhold():
    pr = POLICY["pull_requests"]
    assert {"hold", "on-hold", "do-not-merge"} <= set(pr["hold_labels"])
    assert pr["agents_may_remove_hold_label"] is False
    assert pr["agents_may_merge"] is False
    assert pr["require_dco_signoff"] is True


def test_forbidden_paths_exist():
    for rel in POLICY["paths"]["agent_forbidden"]:
        assert (REPO_ROOT / rel).exists(), rel


def test_architecture_paths_exist():
    arch = POLICY["architecture"]
    for name in arch["stage_shared_modules"]:
        assert (REPO_ROOT / "scripts" / name).is_file(), name
    assert (REPO_ROOT / arch["llm_calls_only_via"]).is_file()
    assert POLICY["testing"]["coverage_gate_source"] == "pyproject.toml"
    assert (REPO_ROOT / "pyproject.toml").is_file()


def test_policy_matches_agents_md():
    agents = (REPO_ROOT / "AGENTS.md").read_text()
    for label in POLICY["pull_requests"]["hold_labels"]:
        assert label in agents
    assert ".github/workflows" in agents
