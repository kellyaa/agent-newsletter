"""Validate the committed GitHub Actions workflows.

Nothing else exercises .github/workflows/*.yml, so a typo or a drifting
convention only shows up as a broken or silently-ungated CI run after merge.
The workflow comments state several invariants (job timeouts protect the
free-tier minutes budget, the coverage gate lives only in pyproject.toml,
the site tests and deploy build share one Node/pnpm toolchain); these tests
pin them.
"""
from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOW_PATHS = sorted(WORKFLOW_DIR.glob("*.yml"))
EXPECTED_WORKFLOWS = {"deploy.yml", "site-tests.yml", "tests.yml"}


def _load(name: str) -> dict:
    with (WORKFLOW_DIR / name).open("rb") as fh:
        return yaml.safe_load(fh)


def _triggers(workflow: dict) -> dict:
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    return workflow.get("on", workflow.get(True))


def _steps(workflow: dict):
    for job in workflow["jobs"].values():
        yield from job["steps"]


def _step_with(workflow: dict, uses_prefix: str) -> dict:
    matches = [
        s for s in _steps(workflow) if str(s.get("uses", "")).startswith(uses_prefix)
    ]
    assert len(matches) == 1, f"expected exactly one {uses_prefix} step"
    return matches[0].get("with", {})


def test_expected_workflows_are_present():
    assert {p.name for p in WORKFLOW_PATHS} == EXPECTED_WORKFLOWS


@pytest.mark.parametrize("path", WORKFLOW_PATHS, ids=lambda p: p.name)
def test_workflow_parses_with_name_trigger_and_jobs(path):
    wf = _load(path.name)
    assert wf["name"]
    assert _triggers(wf), "workflow has no triggers"
    assert wf["jobs"]


@pytest.mark.parametrize("path", WORKFLOW_PATHS, ids=lambda p: p.name)
def test_every_job_has_a_bounded_timeout(path):
    # GitHub's default is 360 minutes; a hung job would burn the whole budget.
    for job_id, job in _load(path.name)["jobs"].items():
        timeout = job.get("timeout-minutes")
        assert isinstance(timeout, int), f"{job_id} has no timeout-minutes"
        assert 0 < timeout <= 15, f"{job_id} timeout {timeout} is out of range"


@pytest.mark.parametrize("path", WORKFLOW_PATHS, ids=lambda p: p.name)
def test_workflow_declares_least_privilege_permissions(path):
    perms = _load(path.name).get("permissions")
    assert isinstance(perms, dict), "top-level permissions block is required"
    assert perms.get("contents") == "read"
    assert "write-all" not in perms.values()


@pytest.mark.parametrize("path", WORKFLOW_PATHS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_version(path):
    for step in _steps(_load(path.name)):
        uses = step.get("uses")
        if uses is None:
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[\w.-]+", uses), (
            f"{uses!r} must be pinned as owner/repo@ref"
        )
        assert not uses.endswith(("@main", "@master")), f"{uses} floats on a branch"


@pytest.mark.parametrize("name", ["tests.yml", "site-tests.yml"])
def test_test_workflows_run_on_pr_and_push_to_main(name):
    triggers = _triggers(_load(name))
    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["push"]["branches"] == ["main"]


@pytest.mark.parametrize("name", ["tests.yml", "site-tests.yml"])
def test_test_workflows_only_cancel_superseded_pull_request_runs(name):
    # Every merge to main must get a clean signal; only PR runs may be cancelled.
    group = _load(name)["concurrency"]
    assert "github.ref" in group["group"]
    assert "pull_request" in str(group["cancel-in-progress"])
    assert "==" in str(group["cancel-in-progress"])


@pytest.mark.parametrize("name", ["tests.yml", "site-tests.yml"])
def test_test_workflows_give_each_push_its_own_concurrency_group(name):
    # A shared group holds only one pending run, so back-to-back merges would
    # cancel each other even with cancel-in-progress false (#262).
    group = _load(name)["concurrency"]["group"]
    assert "github.event_name == 'push' && github.sha" in group


def test_pytest_workflow_does_not_hardcode_the_coverage_gate():
    # The gate is owned by pyproject.toml so local runs and CI cannot diverge.
    runs = [s["run"] for s in _steps(_load("tests.yml")) if "run" in s]
    pytest_runs = [r for r in runs if "pytest" in r]
    assert pytest_runs
    assert not any("--cov-fail-under" in r for r in pytest_runs)


def test_pytest_workflow_python_satisfies_project_requirement():
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        requires = tomllib.load(fh)["project"]["requires-python"]
    minimum = tuple(int(x) for x in re.fullmatch(r">=(\d+\.\d+)", requires).group(1).split("."))
    version = _step_with(_load("tests.yml"), "actions/setup-python")["python-version"]
    assert tuple(int(x) for x in str(version).split(".")) >= minimum


def test_pytest_workflow_installs_the_test_extra():
    runs = [s["run"] for s in _steps(_load("tests.yml")) if "run" in s]
    assert "uv sync --extra test" in runs


def test_pytest_workflow_pr_comment_step_only_runs_on_pull_requests():
    steps = [
        s for s in _steps(_load("tests.yml"))
        if str(s.get("uses", "")).startswith("py-cov-action/")
    ]
    assert len(steps) == 1
    assert "pull_request" in steps[0]["if"]


def test_site_workflows_share_node_and_pnpm_toolchain():
    # Site tests must run on the same toolchain that builds the deployed site.
    site, deploy = _load("site-tests.yml"), _load("deploy.yml")
    assert (
        _step_with(site, "actions/setup-node")["node-version"]
        == _step_with(deploy, "actions/setup-node")["node-version"]
    )
    assert (
        _step_with(site, "pnpm/action-setup")["version"]
        == _step_with(deploy, "pnpm/action-setup")["version"]
    )
    for wf in (site, deploy):
        runs = [s["run"] for s in _steps(wf) if "run" in s]
        assert "pnpm install --frozen-lockfile" in runs


def test_site_workflow_node_version_meets_documented_minimum():
    # package.json `test` script uses --experimental-strip-types (Node 22.6+).
    node = int(str(_step_with(_load("site-tests.yml"), "actions/setup-node")["node-version"]).split(".")[0])
    assert node >= 22


def test_site_tests_workflow_runs_the_package_test_script():
    runs = [s["run"] for s in _steps(_load("site-tests.yml")) if "run" in s]
    assert "pnpm test" in runs


def test_deploy_runs_only_from_main_or_content_and_has_pages_permissions():
    wf = _load("deploy.yml")
    assert _triggers(wf)["push"]["branches"] == ["main", "content"]
    assert wf["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_deploy_builds_main_and_stages_issues_from_content_branch():
    steps = _load("deploy.yml")["jobs"]["build"]["steps"]
    refs = [s["with"]["ref"] for s in steps if str(s.get("uses", "")).startswith("actions/checkout")]
    assert refs == ["main", "content"]


def test_deploy_job_depends_on_build_and_targets_github_pages():
    deploy = _load("deploy.yml")["jobs"]["deploy"]
    assert deploy["needs"] == "build"
    assert deploy["environment"]["name"] == "github-pages"


def test_deploy_site_build_uses_pnpm_build_with_base_url_env():
    steps = _load("deploy.yml")["jobs"]["build"]["steps"]
    build = next(s for s in steps if s.get("run") == "pnpm build")
    assert {"SITE_URL", "SITE_BASE"} <= set(build["env"])


def test_deploy_configures_pages_before_the_build_reads_its_outputs():
    # Step outputs are only visible to later steps; otherwise SITE_URL always
    # falls back to <owner>.github.io (#263).
    steps = _load("deploy.yml")["jobs"]["build"]["steps"]
    ids = [s.get("id") for s in steps]
    build = next(i for i, s in enumerate(steps) if s.get("run") == "pnpm build")
    assert "steps.pages.outputs" in steps[build]["env"]["SITE_URL"]
    assert ids.index("pages") < build


def _stage_content(tmp_path: Path, content: dict[str, str]) -> set[str]:
    # Run the deploy workflow's staging step against a fake _content checkout.
    steps = _load("deploy.yml")["jobs"]["build"]["steps"]
    script = next(s for s in steps if s.get("name") == "Stage content from content branch")["run"]
    for rel, text in content.items():
        (tmp_path / "_content" / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "_content" / rel).write_text(text)
    subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, check=True)
    issues = tmp_path / "site" / "src" / "content" / "issues"
    return {p.relative_to(issues).as_posix() for p in issues.rglob("*.md")}


def test_deploy_stages_each_edition_into_its_own_issues_folder(tmp_path):
    staged = _stage_content(tmp_path, {
        "editions/agents/issues/2026-10-01.md": "a",
        "editions/agents/state.db": "db",
        "editions/engineering-with-ai/issues/2026-10-02.md": "b",
        "editions/_template/issues/example.md": "t",
    })
    assert staged == {"agents/2026-10-01.md", "engineering-with-ai/2026-10-02.md"}


def test_deploy_stages_the_legacy_flat_layout_until_content_is_migrated(tmp_path):
    staged = _stage_content(tmp_path, {"site/src/content/issues/2026-10-01.md": "a"})
    assert staged == {"2026-10-01.md"}


def test_deploy_ignores_the_legacy_layout_once_editions_exist(tmp_path):
    staged = _stage_content(tmp_path, {
        "editions/agents/issues/2026-10-02.md": "a",
        "site/src/content/issues/2026-10-01.md": "stale",
    })
    assert staged == {"agents/2026-10-02.md"}


def test_deploy_mentions_editions_so_migrate_layout_will_run():
    # scripts/migrate_layout.py (#301) refuses to migrate unless deploy.yml
    # contains "editions/".
    assert "editions/" in (WORKFLOW_DIR / "deploy.yml").read_text()
