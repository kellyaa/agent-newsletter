"""The content-branch shell contract lives only in ``scripts/contentbranch.sh``.

run.sh creates the ``content`` worktree and run_edition.sh commits each run to
it as ``newsletter(<slug>): <date> daily run``. The root watchdog.sh reads those
commits back with a regex to decide whether the pipeline is stuck, and
scripts/watchdog.sh points ``CONTENT_ROOT`` at the worktree for missed_days.py /
dead_sources.py. Each spelled the path and the subject by hand, and nothing
checked the watchdog's regex against the writer: a subject change that kept
models/backfill in step passed CI, then made the hourly watchdog report a
healthy pipeline as stuck. Moving the path in run.sh alone made the MISSED: /
DEAD: checks read the repo root and print nothing (#665).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from models import LEGACY_EDITION, LEGACY_RUN_COMMIT_PREFIX, run_commit_prefix

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "contentbranch.sh"
SHELL = sorted({*REPO.glob("*.sh"), *(REPO / "scripts").glob("*.sh"),
                *(REPO / "launchd").glob("*.sh")} - {LIB})
SOURCE_LINE = '. "$REPO_ROOT/scripts/contentbranch.sh"'
# Who must source the library, and which helper each must use.
USERS = {
    "run.sh": ['CONTENT_WORKTREE="$(content_worktree_path "$REPO_ROOT")"'],
    "watchdog.sh": ['$(content_worktree_path "$REPO_ROOT")', '--grep="$CONTENT_DAILY_RUN_RE"'],
    "scripts/watchdog.sh": ['$(content_worktree_path "$REPO_ROOT")'],
    "scripts/run_edition.sh": ['$(content_run_subject "$EDITION" "$DATE_TODAY")',
                               '$(content_failed_subject "$EDITION" "$DATE_TODAY" "$RUN_ATTEMPT" "$1")'],
}


def _sh(script: str, *args: str) -> str:
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"; {script}', "_", *args],
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout.rstrip("\n")


def _daily_re() -> str:
    return _sh('printf "%s" "$CONTENT_DAILY_RUN_RE"')


def _matches(subject: str) -> bool:
    """Match like `git log -E --grep` does (POSIX ERE, via grep -E)."""
    return subprocess.run(["grep", "-qE", _daily_re()], input=subject + "\n",
                          text=True).returncode == 0


def _code(p: Path) -> str:
    return "\n".join(l for l in p.read_text().splitlines() if not l.lstrip().startswith("#"))


# ─── the library itself ────────────────────────────────────────────────────

def test_worktree_path():
    assert _sh('content_worktree_path "$1"', "/r") == "/r/.worktrees/content"


@pytest.mark.parametrize("slug", [LEGACY_EDITION, "beta", "engineering-with-ai"])
def test_subjects_start_with_the_models_prefix(slug):
    """backfill.find_pre_run_commit matches models.run_commit_prefix + date."""
    run = _sh('content_run_subject "$1" "$2"', slug, "2026-07-09")
    failed = _sh('content_failed_subject "$@"', slug, "2026-07-09", "3", "rank")
    assert run == f"{run_commit_prefix(slug)}2026-07-09 daily run"
    assert failed == f"{run_commit_prefix(slug)}2026-07-09 attempt 3 failed at rank"


@pytest.mark.parametrize("slug", [LEGACY_EDITION, "beta", "engineering-with-ai"])
def test_daily_run_regex_matches_what_the_writer_writes(slug):
    assert _matches(_sh('content_run_subject "$1" "$2"', slug, "2026-07-09"))
    assert not _matches(_sh('content_failed_subject "$@"', slug, "2026-07-09", "1", "write"))


@pytest.mark.parametrize("subject,ok", [
    (f"{LEGACY_RUN_COMMIT_PREFIX}2026-07-09 daily run", True),  # pre multi-edition history
    ("newsletter: 2026-07-09 attempt 2 failed at rank", False),
    ("newsletter: persist state.db from interrupted 2026-07-09 run", False),
    ("newsletter(agents): 2026-07-09 daily run (partial)", False),
    ("docs: describe the daily run", False),
])
def test_daily_run_regex_vocabulary(subject, ok):
    assert _matches(subject) is ok


# ─── single source ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("script", SHELL, ids=lambda p: str(p.relative_to(REPO)))
def test_no_script_spells_the_contract(script):
    code = _code(script)
    assert ".worktrees/content" not in code, (
        f"{script.name} spells the content worktree path; use content_worktree_path")
    assert "newsletter(" not in code and "newsletter: " not in code, (
        f"{script.name} spells a run-commit subject; use content_run_subject / "
        "content_failed_subject")
    assert "daily run$" not in code and "DAILY_RUN_PATTERN" not in code, (
        f"{script.name} has its own daily-run regex; use CONTENT_DAILY_RUN_RE")


@pytest.mark.parametrize("name", sorted(USERS))
def test_users_source_the_library_and_use_its_helpers(name):
    text = (REPO / name).read_text()
    assert SOURCE_LINE in text, name
    for needle in USERS[name]:
        assert needle in text, f"{name}: expected {needle!r}"


def test_detector_flags_inline_contract(tmp_path):
    bad = tmp_path / "bad.sh"
    for line in ('W="$REPO_ROOT/.worktrees/content"',
                 'git commit -m "newsletter($EDITION): $DATE_TODAY daily run"',
                 "P='^newsletter(\\([a-z0-9-]+\\))?:.* daily run$'"):
        bad.write_text(f"# comments are fine: .worktrees/content\n{line}\n")
        with pytest.raises(AssertionError):
            test_no_script_spells_the_contract(bad)
    bad.write_text("# .worktrees/content and newsletter(x): only in a comment\n")
    test_no_script_spells_the_contract(bad)


# ─── writer -> root watchdog.sh, through real git ─────────────────────────

def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = os.environ.copy()
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)


def _watchdog_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "watchdog.sh", repo / "watchdog.sh")
    for lib in ("runlog.sh", "contentbranch.sh"):
        shutil.copy2(REPO / "scripts" / lib, repo / "scripts" / lib)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "marker").write_text("x")
    _git(repo, "add", "marker")
    _git(repo, "commit", "-qm", "manual maintenance")
    _git(repo, "checkout", "-q", "-b", "content")
    return repo


def _content_commit(repo: Path, subject: str, date: str | None = None) -> None:
    (repo / "c").write_text(subject)
    _git(repo, "add", "c")
    _git(repo, "commit", "-qm", subject, date=date)


def _run_watchdog(repo: Path) -> subprocess.CompletedProcess[str]:
    _git(repo, "checkout", "-q", "main")
    env = {**os.environ, "STALE_HOURS": "36", "PATH": f"{repo / 'bin'}{os.pathsep}{os.environ['PATH']}"}
    env.pop("CONTENT_WORKTREE", None)
    env.pop("WATCHDOG_WEBHOOK_URL", None)
    (repo / "bin").mkdir(exist_ok=True)
    for tool in ("osascript", "notify-send"):
        (repo / "bin" / tool).write_text("#!/bin/sh\nexit 0\n")
        (repo / "bin" / tool).chmod(0o755)
    return subprocess.run(["bash", str(repo / "watchdog.sh")], cwd=repo, env=env,
                          capture_output=True, text=True, check=True, timeout=30)


def test_writer_subject_keeps_the_root_watchdog_healthy(tmp_path):
    repo = _watchdog_repo(tmp_path)
    _content_commit(repo, _sh('content_run_subject "$1" "$2"', "beta", "2026-07-09"))
    p = _run_watchdog(repo)
    assert "watchdog: ok" in p.stdout, p.stdout + p.stderr


def test_writer_failed_subject_does_not_reset_the_clock(tmp_path):
    repo = _watchdog_repo(tmp_path)
    _content_commit(repo, _sh('content_run_subject "$1" "$2"', "beta", "2000-01-01"),
                    date="2000-01-01T00:00:00+00:00")
    _content_commit(repo, _sh('content_failed_subject "$@"', "beta", "2000-01-02", "1", "rank"))
    p = _run_watchdog(repo)
    assert "STALE" in p.stdout, p.stdout + p.stderr


# ─── run.sh's worktree is where scripts/watchdog.sh looks ─────────────────

def test_scripts_watchdog_reads_the_worktree_run_sh_creates(tmp_path):
    """scripts/watchdog.sh exports CONTENT_ROOT=<the run.sh worktree> to its readers."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for f in ("watchdog.sh", "runlog.sh", "runlock.sh", "contentbranch.sh", "editionlist.sh", "edition.py", "models.py"):
        shutil.copy2(REPO / "scripts" / f, repo / "scripts" / f)
    (repo / "editions" / "agents").mkdir(parents=True)
    shutil.copy2(REPO / "editions" / "agents" / "edition.yaml",
                 repo / "editions" / "agents" / "edition.yaml")
    # Stand-ins keep the readers' contract: prefixed finding lines, exit 1 (#673).
    for reader, prefix in (("missed_days.py", "MISSED:"), ("dead_sources.py", "DEAD:")):
        (repo / "scripts" / reader).write_text(
            f"import os, sys\nprint('{prefix} ROOT=' + os.environ.get('CONTENT_ROOT', ''))\nsys.exit(1)\n")
    (repo / "run.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    wt = Path(_sh('content_worktree_path "$1"', str(repo)))
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    env = {k: v for k, v in os.environ.items() if k != "CONTENT_ROOT"}
    env["NEWSLETTER_PYTHON"] = sys.executable
    p = subprocess.run(["bash", str(repo / "scripts" / "watchdog.sh"), "--check"], cwd=repo,
                       env=env, capture_output=True, text=True, timeout=60)
    roots = re.findall(r"^(?:MISSED|DEAD): (ROOT=.*)$", p.stdout, re.M)
    assert roots == [f"ROOT={wt}", f"ROOT={wt}"], p.stdout + p.stderr
