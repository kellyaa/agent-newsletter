"""scripts/generate-issues.mjs (ACMM acmm:auto-issue-gen) turns a markdown backlog into issues."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "generate-issues.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

BACKLOG = """# Strategist backlog

Intro prose the parser must ignore, including a `#` line and a ### subheading
below so only `## ` headings become issues.

## Record per-source fetch outcomes

Body of the first issue.

### Not a separate issue

## Add DEAD: lines to the watchdog

Second issue body.
"""


def _node_eval(expr: str):
    """Evaluate `expr` with the script's exports bound to `mod`."""
    r = subprocess.run(
        ["node", "--input-type=module", "-e", f"import * as mod from {SCRIPT.as_uri()!r}; console.log({expr})"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(r.stdout)


def test_parse_backlog_reads_headings_and_skips_subheadings():
    issues = _node_eval(f"JSON.stringify(mod.parseBacklog({BACKLOG!r}))")
    assert [i["title"] for i in issues] == [
        "Record per-source fetch outcomes",
        "Add DEAD: lines to the watchdog",
    ]
    assert issues[0]["body"].startswith("Body of the first issue.")
    assert "Not a separate issue" in issues[0]["body"]


def test_plan_issues_skips_titles_already_open():
    plan = _node_eval(
        f"JSON.stringify(mod.planIssues(mod.parseBacklog({BACKLOG!r}),"
        "['add dead: lines to the watchdog']))"
    )
    assert [i["title"] for i in plan["toCreate"]] == ["Record per-source fetch outcomes"]
    assert [i["title"] for i in plan["skipped"]] == ["Add DEAD: lines to the watchdog"]


def _run(tmp_path: Path, backlog: Path, *args: str, gh_dir: Path | None = None):
    env = {**os.environ, "PATH": f"{gh_dir}:{os.environ['PATH']}"} if gh_dir else os.environ
    return subprocess.run(
        ["node", str(SCRIPT), str(backlog), *args], cwd=tmp_path,
        capture_output=True, text=True, env=env,
    )


def test_dry_run_prints_plan_and_skips_open(tmp_path):
    backlog = tmp_path / "backlog.md"
    backlog.write_text(BACKLOG)
    gh_dir = tmp_path / "bin"
    gh_dir.mkdir()
    log = _stub_gh_with_open(gh_dir, ["Add DEAD: lines to the watchdog"])
    r = _run(tmp_path, backlog, gh_dir=gh_dir)
    assert r.returncode == 0
    assert "would create: Record per-source fetch outcomes" in r.stdout
    assert "skip (already open): Add DEAD: lines to the watchdog" in r.stdout
    assert "(dry run; pass --create to file them)" in r.stdout
    assert not log.exists()  # dry run never creates


def test_options_before_backlog_file(tmp_path):
    backlog = tmp_path / "backlog.md"
    backlog.write_text(BACKLOG)
    gh_dir = tmp_path / "bin"
    gh_dir.mkdir()
    _stub_gh_with_open(gh_dir, [])
    env = {**os.environ, "PATH": f"{gh_dir}:{os.environ['PATH']}"}
    r = subprocess.run(
        ["node", str(SCRIPT), "--repo", "kellyaa/agent-newsletter", "--label", "x", str(backlog)],
        cwd=tmp_path, capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "would create:" in r.stdout


def _stub_gh_with_open(dir_: Path, titles: list[str]) -> Path:
    """A fake `gh` answering `issue list` with `titles`; `issue create` logs its argv."""
    log = dir_.parent / "gh-calls.log"
    gh = dir_ / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "issue list" ]; then\n'
        f"  printf '%s' '{json.dumps([{'title': t} for t in titles])}'\n"
        "elif [ \"$1 $2\" = \"issue create\" ]; then\n"
        f'  printf "%s\\n" "$*" >> "{log}"; echo https://example/issue/1\n'
        "fi\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    return log


def test_create_files_issues_with_labels_and_body(tmp_path):
    backlog = tmp_path / "backlog.md"
    backlog.write_text(BACKLOG)
    gh_dir = tmp_path / "bin"
    gh_dir.mkdir()
    log = _stub_gh_with_open(gh_dir, [])
    r = _run(
        tmp_path, backlog, "--label", "help wanted", "--repo", "kellyaa/agent-newsletter",
        "--create", gh_dir=gh_dir,
    )
    assert r.returncode == 0, r.stderr
    assert "created: Record per-source fetch outcomes" in r.stdout
    assert "created: Add DEAD: lines to the watchdog" in r.stdout
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert (
        "create --title Record per-source fetch outcomes --body-file - "
        "--label help wanted --repo kellyaa/agent-newsletter" in calls[0]
    )
    # the body travels on stdin (--body-file -), not on the command line
    assert "Body of the first issue." not in calls[0]


def test_usage_and_empty_backlog_exit_two(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("# nothing but prose\n")
    r = subprocess.run(["node", str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 2
    assert "usage:" in r.stderr
    r = _run(tmp_path, empty)
    assert r.returncode == 2
    assert "no '## ' issue headings found" in r.stderr


def test_script_is_listed_in_readme():
    assert "generate-issues.mjs" in (REPO_ROOT / "README.md").read_text()
