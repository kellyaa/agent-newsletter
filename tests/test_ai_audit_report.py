"""scripts/ai-audit-report.mjs (ACMM aef:audit-trail) reports unsigned commits."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ai-audit-report.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _run(cwd: Path, *args: str):
    return subprocess.run(
        ["node", str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True
    )


def test_reports_unsigned_commit_and_check_fails(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-s", "-m", "signed")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "unsigned one")
    r = _run(tmp_path)
    assert r.returncode == 0
    assert "Commits: 2" in r.stdout
    assert "Missing DCO sign-off: 1" in r.stdout
    assert "unsigned one" in r.stdout
    assert _run(tmp_path, "--check").returncode == 1


def test_check_passes_when_all_signed_and_counts_automated(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-s", "-m", "a",
         "-m", "Co-authored-by: Claude <noreply@anthropic.com>")
    r = _run(tmp_path, "--check")
    assert r.returncode == 0
    assert "Automated-contributor commits: 1" in r.stdout
    assert "Missing DCO sign-off: 0" in r.stdout


def test_script_is_listed_in_readme():
    assert "ai-audit-report.mjs" in (REPO_ROOT / "README.md").read_text()
