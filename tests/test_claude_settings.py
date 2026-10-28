"""``.claude/settings.json`` must stay valid and keep the guard-rails the docs promise."""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())


def test_permissions_shape():
    perms = SETTINGS["permissions"]
    assert set(perms) <= {"allow", "deny"}
    for rules in perms.values():
        assert rules and all(isinstance(r, str) for r in rules)


def test_denies_secrets_workflows_and_merging():
    deny = set(SETTINGS["permissions"]["deny"])
    assert {"Read(./.env)", "Edit(./.github/workflows/**)", "Bash(gh pr merge:*)"} <= deny


def test_deny_wins_over_allow():
    perms = SETTINGS["permissions"]
    assert not set(perms["allow"]) & set(perms["deny"])


def test_denied_workflow_path_matches_agent_policy():
    import yaml

    policy = yaml.safe_load((REPO_ROOT / "policies" / "agent-policy.yaml").read_text())
    for rel in policy["paths"]["agent_forbidden"]:
        assert f"Edit(./{rel}/**)" in SETTINGS["permissions"]["deny"]


def test_session_summary_and_docs_exist():
    for rel in (".claude/session-summary.md", "docs/review-rubric.md", "docs/quality.md", "docs/metrics.md"):
        assert (REPO_ROOT / rel).is_file(), rel
