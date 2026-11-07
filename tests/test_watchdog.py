"""Behavioral tests for the watchdog's stale-run decisions."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path



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


def test_per_edition_daily_run_commit_counts_as_fresh(tmp_path):
    repo = _repo_with_main_edit(tmp_path)
    _content_commit(repo, "newsletter(sdlc): 2026-10-23 daily run")

    result = _run_watchdog(repo)

    assert "watchdog: ok" in result.stdout
    assert not (repo / "logs" / "watchdog-last-fire").exists()


def test_per_edition_failed_attempt_commit_does_not_reset_the_clock(tmp_path):
    repo = _repo_with_main_edit(tmp_path)
    _content_commit(repo, "newsletter(agents): 2000-01-01 daily run",
                    date=_old_commit_date())
    _content_commit(repo, "newsletter(sdlc): 2026-10-23 attempt 1 failed at rank")

    result = _run_watchdog(repo)

    assert "watchdog: STALE" in result.stdout


def test_main_head_with_per_edition_newsletter_subject_is_a_baseline(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter(agents): 2000-01-01 daily run",
                          commit_date=_old_commit_date())

    result = _run_watchdog(repo)

    assert "not a newsletter commit" not in result.stdout
    assert "watchdog: STALE" in result.stdout


# ─── notification, throttle window and thresholds ─────────────────────────
# The fake osascript above discards its arguments, so nothing pinned that a
# stale pipeline actually notifies (or that healthy/throttled runs stay quiet).


def _run_recording(repo: Path, *, stale_hours: int = 36,
                   extra_env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess, Path]:
    """Run watchdog.sh with an osascript stub that logs each call's argv."""
    bin_dir = repo / "rec-bin"
    bin_dir.mkdir(exist_ok=True)
    calls = repo / "osascript-calls.txt"
    stub = bin_dir / "osascript"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{calls}"\nexit 0\n')
    stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["STALE_HOURS"] = str(stale_hours)
    env.update(extra_env or {})
    p = subprocess.run(["bash", str(repo / "watchdog.sh")], cwd=repo, env=env,
                       capture_output=True, text=True, check=True)
    return p, calls


def _hours_ago(hours: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - hours * 3600))


def test_stale_run_sends_notification_with_age_and_log_hint(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run",
                          commit_date=_old_commit_date())

    _, calls = _run_recording(repo)

    sent = calls.read_text()
    assert "display notification" in sent
    assert "Newsletter pipeline appears stuck" in sent
    assert "Check logs/run-*.log." in sent


def test_healthy_run_sends_no_notification(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run")

    _, calls = _run_recording(repo)

    assert not calls.exists()


def test_throttled_run_sends_no_notification(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run",
                          commit_date=_old_commit_date())
    state = repo / "logs" / "watchdog-last-fire"
    state.parent.mkdir(parents=True)
    state.write_text(f"{int(time.time()) - 60}\n")

    _, calls = _run_recording(repo)

    assert not calls.exists()


def test_fire_older_than_four_hours_ago_notifies_again(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run",
                          commit_date=_old_commit_date())
    state = repo / "logs" / "watchdog-last-fire"
    state.parent.mkdir(parents=True)
    state.write_text(f"{int(time.time()) - 4 * 3600 - 60}\n")

    p, calls = _run_recording(repo)

    assert "watchdog: STALE" in p.stdout
    assert "throttled" not in p.stdout
    assert "display notification" in calls.read_text()
    assert abs(int(state.read_text()) - int(time.time())) <= 5


def test_fire_just_inside_four_hour_window_is_still_throttled(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run",
                          commit_date=_old_commit_date())
    state = repo / "logs" / "watchdog-last-fire"
    state.parent.mkdir(parents=True)
    state.write_text(f"{int(time.time()) - 4 * 3600 + 120}\n")

    p, calls = _run_recording(repo)

    assert "throttled" in p.stdout
    assert not calls.exists()


def test_commit_just_inside_stale_hours_is_ok(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run",
                          commit_date=_hours_ago(35.5))

    p, calls = _run_recording(repo, stale_hours=36)

    assert "watchdog: ok (last commit 35h ago)" in p.stdout
    assert not calls.exists()


def test_commit_at_stale_hours_is_stale(tmp_path):
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run",
                          commit_date=_hours_ago(36.5))

    p, calls = _run_recording(repo, stale_hours=36)

    assert "watchdog: STALE — last commit 36h ago" in p.stdout
    assert "display notification" in calls.read_text()


def test_notification_text_escapes_double_quotes(tmp_path):
    """notify() backslash-escapes `"` so the AppleScript string stays closed."""
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run",
                          commit_date=_old_commit_date())
    script = (repo / "watchdog.sh").read_text()
    script = script.replace('"Newsletter pipeline appears stuck"', '\'Say "hi"\'')
    (repo / "watchdog.sh").write_text(script)

    _, calls = _run_recording(repo)

    assert 'with title \\"' not in calls.read_text()
    assert 'with title "Say \\"hi\\""' in calls.read_text()


def test_content_worktree_is_preferred_over_refs(tmp_path):
    """A live CONTENT_WORKTREE decides staleness, not the main-branch HEAD."""
    repo = _repo_with_main_edit(tmp_path)
    _content_commit(repo, "newsletter(agents): 2000-01-01 daily run",
                    date=_old_commit_date())
    wt = tmp_path / "content-wt"
    _git(repo, "worktree", "add", "-q", str(wt), "content")

    p, _ = _run_recording(repo, extra_env={"CONTENT_WORKTREE": str(wt)})

    assert "using newest daily-run commit on the content branch" in p.stdout
    assert "watchdog: STALE" in p.stdout


def test_no_notification_tool_still_records_the_fire(tmp_path):
    """Without osascript (Linux) the run must not fail; it still throttles."""
    repo = _watchdog_repo(tmp_path, subject="newsletter: daily run",
                          commit_date=_old_commit_date())
    env = os.environ.copy()
    env["STALE_HOURS"] = "36"
    bin_dir = repo / "min-bin"
    bin_dir.mkdir()
    for tool in ("bash", "git", "date", "cat", "mkdir", "dirname", "sort", "head"):
        src = shutil.which(tool)
        assert src, tool
        (bin_dir / tool).symlink_to(src)
    env["PATH"] = str(bin_dir)

    p = subprocess.run([str(bin_dir / "bash"), str(repo / "watchdog.sh")], cwd=repo, env=env,
                       capture_output=True, text=True)

    assert p.returncode == 0, p.stderr
    assert "watchdog: STALE" in p.stdout
    assert (repo / "logs" / "watchdog-last-fire").exists()
