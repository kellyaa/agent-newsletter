"""Behavioral tests for the watchdog's stale-run decisions."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest


WATCHDOG = Path(__file__).resolve().parents[1] / "watchdog.sh"


def _watchdog_repo(tmp_path: Path, *, subject: str | None = None,
                   commit_date: str | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(WATCHDOG, repo / "watchdog.sh")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if subject is not None:
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        (repo / "marker").write_text("test\n")
        subprocess.run(["git", "-C", str(repo), "add", "marker"], check=True)
        env = os.environ.copy()
        if commit_date is not None:
            env["GIT_AUTHOR_DATE"] = commit_date
            env["GIT_COMMITTER_DATE"] = commit_date
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-qm", subject],
            check=True,
            env=env,
        )
    return repo


def _run_watchdog(repo: Path, *, stale_hours: int = 36) -> subprocess.CompletedProcess:
    bin_dir = repo / "test-bin"
    bin_dir.mkdir(exist_ok=True)
    fake_osascript = bin_dir / "osascript"
    fake_osascript.write_text("#!/bin/sh\nexit 0\n")
    fake_osascript.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["STALE_HOURS"] = str(stale_hours)
    return subprocess.run(
        ["bash", str(repo / "watchdog.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def _old_commit_date() -> str:
    return "2000-01-01T00:00:00+00:00"


def test_no_git_history_exits_without_creating_state(tmp_path):
    repo = tmp_path / "not-a-repository"
    repo.mkdir()
    shutil.copy2(WATCHDOG, repo / "watchdog.sh")

    result = _run_watchdog(repo)

    assert "no git history" in result.stdout
    assert not (repo / "logs" / "watchdog-last-fire").exists()


def test_non_newsletter_head_skips_staleness_check(tmp_path):
    repo = _watchdog_repo(
        tmp_path,
        subject="manual maintenance",
        commit_date=_old_commit_date(),
    )

    result = _run_watchdog(repo)

    assert "HEAD is not a newsletter commit" in result.stdout
    assert not (repo / "logs" / "watchdog-last-fire").exists()


def test_recent_newsletter_commit_is_healthy(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run")

    result = _run_watchdog(repo)

    assert "watchdog: ok" in result.stdout
    assert not (repo / "logs" / "watchdog-last-fire").exists()


def test_stale_newsletter_commit_fires_and_records_timestamp(tmp_path):
    repo = _watchdog_repo(
        tmp_path,
        subject="newsletter: daily run",
        commit_date=_old_commit_date(),
    )

    result = _run_watchdog(repo)

    assert "watchdog: STALE" in result.stdout
    last_fire = int((repo / "logs" / "watchdog-last-fire").read_text())
    assert abs(last_fire - int(time.time())) <= 5


def test_stale_newsletter_commit_is_throttled_after_recent_fire(tmp_path):
    repo = _watchdog_repo(
        tmp_path,
        subject="newsletter: daily run",
        commit_date=_old_commit_date(),
    )
    state = repo / "logs" / "watchdog-last-fire"
    state.parent.mkdir(parents=True)
    recent_fire = int(time.time()) - 60
    state.write_text(f"{recent_fire}\n")

    result = _run_watchdog(repo)

    assert "stale" in result.stdout
    assert "throttled" in result.stdout
    assert state.read_text() == f"{recent_fire}\n"


# ─── content-branch awareness (#305) ──────────────────────────────────────
# run.sh commits every daily run to the `content` branch, never to main.


def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = os.environ.copy()
    if date is not None:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                   capture_output=True)


def _content_commit(repo: Path, subject: str, date: str | None = None) -> None:
    """Commit `subject` on the `content` branch, leaving main checked out."""
    has_content = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", "content"],
        capture_output=True,
    ).returncode == 0
    _git(repo, "checkout", "-q", *([] if has_content else ["-b"]), "content")
    (repo / "content-marker").write_text(subject)
    _git(repo, "add", "content-marker")
    _git(repo, "commit", "-qm", subject, date=date)
    _git(repo, "checkout", "-q", "-")


def _repo_with_main_edit(tmp_path: Path) -> Path:
    """A repo whose main HEAD is a recent human edit, not a newsletter commit."""
    repo = _watchdog_repo(tmp_path, subject="manual maintenance")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    return repo


def test_recent_content_commit_is_healthy_when_main_head_is_a_code_edit(tmp_path):
    repo = _repo_with_main_edit(tmp_path)
    _content_commit(repo, "newsletter: 2026-10-23 daily run")

    result = _run_watchdog(repo)

    assert "content branch" in result.stdout
    assert "watchdog: ok" in result.stdout
    assert not (repo / "logs" / "watchdog-last-fire").exists()


def test_stale_content_commit_fires_even_when_main_head_is_a_code_edit(tmp_path):
    repo = _repo_with_main_edit(tmp_path)
    _content_commit(repo, "newsletter: 2000-01-01 daily run",
                    date=_old_commit_date())

    result = _run_watchdog(repo)

    assert "watchdog: STALE" in result.stdout
    assert (repo / "logs" / "watchdog-last-fire").exists()


def test_failure_commits_on_content_do_not_reset_the_staleness_clock(tmp_path):
    repo = _repo_with_main_edit(tmp_path)
    _content_commit(repo, "newsletter: 2000-01-01 daily run",
                    date=_old_commit_date())
    # A fresh *failed* attempt is not a successful run.
    _content_commit(repo, "newsletter: 2026-10-23 attempt 2 failed at rank")

    result = _run_watchdog(repo)

    assert "watchdog: STALE" in result.stdout


def test_content_branch_read_from_remote_tracking_ref(tmp_path):
    repo = _repo_with_main_edit(tmp_path)
    _content_commit(repo, "newsletter: 2026-10-23 daily run")
    # Simulate a clone that only has origin/content: move the branch to a
    # remote-tracking ref and drop the local one.
    _git(repo, "update-ref", "refs/remotes/origin/content", "content")
    _git(repo, "branch", "-q", "-D", "content")

    result = _run_watchdog(repo)

    assert "watchdog: ok" in result.stdout
