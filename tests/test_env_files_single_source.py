"""The env-file contract lives only in ``scripts/envfiles.sh``.

Only ``run.sh`` used to read ``~/.config/agent-newsletter/env`` and ``.env``,
and it read them after taking the run lock and choosing the Python runner. A
``STALE_HOURS`` or ``WATCHDOG_WEBHOOK_URL`` set only in ``.env`` was ignored by
the root ``watchdog.sh``. The ``flock not found`` alert went out before the
webhook was known. And a ``NEWSLETTER_PYTHON`` in ``.env`` replaced the runner
run.sh had already chosen and logged. ``envfiles_load`` is now the one loader,
and every caller runs it first.
"""
from __future__ import annotations

import os
import re
import shutil
import time
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "envfiles.sh"
SHELL = sorted({*REPO.glob("*.sh"), *(REPO / "scripts").glob("*.sh"),
                *(REPO / "launchd").glob("*.sh")} - {LIB})
CALLERS = (REPO / "run.sh", REPO / "watchdog.sh", REPO / "scripts" / "watchdog.sh")


def _load(root: Path, home: Path, script: str = "") -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ENVT_")}
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; . "{LIB}"; envfiles_load "$1"; {script}', "_", str(root)],
        capture_output=True, text=True, timeout=10, env={**env, "HOME": str(home)},
    )


def _files(tmp_path: Path, user: str | None, repo: str | None) -> tuple[Path, Path]:
    root, home = tmp_path / "repo", tmp_path / "home"
    root.mkdir()
    home.mkdir()
    if user is not None:
        (home / ".config" / "agent-newsletter").mkdir(parents=True)
        (home / ".config" / "agent-newsletter" / "env").write_text(user)
    if repo is not None:
        (root / ".env").write_text(repo)
    return root, home


# ─── behaviour ─────────────────────────────────────────────────────────────

def test_repo_env_overrides_machine_env_and_both_are_exported(tmp_path):
    root, home = _files(tmp_path, "ENVT_A=user\nENVT_B=user\n", "ENVT_B=repo\n")
    p = _load(root, home, 'bash -c \'echo "$ENVT_A $ENVT_B"\'')
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "user repo"


@pytest.mark.parametrize("user,repo,want", [
    (None, None, ""), ("ENVT_A=user\n", None, "user"), (None, "ENVT_A=repo\n", "repo")])
def test_missing_files_are_skipped(tmp_path, user, repo, want):
    root, home = _files(tmp_path, user, repo)
    p = _load(root, home, 'echo "${ENVT_A:-}"')
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == want


def test_unset_home_skips_the_machine_file_under_set_u(tmp_path):
    root, _ = _files(tmp_path, None, "ENVT_A=repo\n")
    env = {k: v for k, v in os.environ.items() if k != "HOME" and not k.startswith("ENVT_")}
    p = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; . "{LIB}"; envfiles_load "$1"; echo "$ENVT_A"', "_", str(root)],
        capture_output=True, text=True, timeout=10, env=env)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "repo"


def test_allexport_is_off_after_loading(tmp_path):
    root, home = _files(tmp_path, None, "ENVT_A=repo\n")
    p = _load(root, home, 'case $- in *a*) echo on ;; *) echo off ;; esac')
    assert p.stdout.strip() == "off"


# ─── single source ─────────────────────────────────────────────────────────

def _code(p: Path) -> str:
    return "\n".join(l for l in p.read_text().splitlines() if not l.lstrip().startswith("#"))


@pytest.mark.parametrize("script", SHELL, ids=lambda p: str(p.relative_to(REPO)))
def test_no_other_script_spells_the_env_files(script):
    code = _code(script)
    assert ".config/agent-newsletter" not in code, (
        f"{script.name} spells the machine env file; use envfiles_load from scripts/envfiles.sh")
    assert not re.search(r'/\.env\b', code), (
        f"{script.name} spells .env; use envfiles_load from scripts/envfiles.sh")


# The first line in each caller that depends on a setting the env files may hold.
FIRST_USE = {
    "run.sh": re.compile(r"^\s*(runlock_acquire|pyrunner_select|notify)\b", re.M),
    "watchdog.sh": re.compile(r"STALE_HOURS=|^\s*notify\b", re.M),
    "scripts/watchdog.sh": re.compile(r"^\s*(pyrunner_select|notify)\b", re.M),
}


@pytest.mark.parametrize("path", CALLERS, ids=lambda p: str(p.relative_to(REPO)))
def test_callers_load_the_env_files_before_first_use(path):
    code = _code(path)
    load = re.search(r'^\. "\$REPO_ROOT/scripts/envfiles\.sh"\nenvfiles_load "\$REPO_ROOT"$', code, re.M)
    assert load, f"{path.name} must source scripts/envfiles.sh and call envfiles_load"
    first = FIRST_USE[str(path.relative_to(REPO))].search(code)
    assert first and load.start() < first.start(), (
        f"{path.name} reads a setting before envfiles_load")


# ─── end to end: the root watchdog honours STALE_HOURS from .env ──────────

def _git(repo: Path, *args: str, date: str | None = None) -> None:
    env = os.environ.copy()
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env, capture_output=True)


def _stale_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "watchdog.sh", repo / "watchdog.sh")
    for lib in ("runlog.sh", "contentbranch.sh", "envfiles.sh"):
        shutil.copy2(REPO / "scripts" / lib, repo / "scripts" / lib)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "user.email", "t@example.com")
    (repo / "c").write_text("x")
    _git(repo, "add", "c")
    # 48h old: stale under the default 36h, fresh under STALE_HOURS=100.
    when = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 48 * 3600)) + "Z"
    _git(repo, "commit", "-qm", "newsletter(agents): 2026-07-09 daily run", date=when)
    return repo


def _watchdog(repo: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("STALE_HOURS", "CONTENT_WORKTREE", "WATCHDOG_WEBHOOK_URL")}
    (repo / "bin").mkdir(exist_ok=True)
    for tool in ("osascript", "notify-send"):
        (repo / "bin" / tool).write_text("#!/bin/sh\nexit 0\n")
        (repo / "bin" / tool).chmod(0o755)
    env.update(HOME=str(home), PATH=f"{repo / 'bin'}{os.pathsep}{os.environ['PATH']}")
    return subprocess.run(["bash", str(repo / "watchdog.sh")], cwd=repo, env=env,
                          capture_output=True, text=True, timeout=30, check=True)


def test_root_watchdog_reads_stale_hours_from_dot_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _stale_repo(tmp_path)
    assert "STALE" in _watchdog(repo, home).stdout
    (repo / "logs" / "watchdog-last-fire").unlink()
    (repo / ".env").write_text("STALE_HOURS=100\n")
    assert "watchdog: ok (last commit 48h ago)" in _watchdog(repo, home).stdout


def test_root_watchdog_posts_to_webhook_set_only_in_dot_env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    repo = _stale_repo(tmp_path)
    (repo / "bin").mkdir()
    (repo / "bin" / "curl").write_text(f'#!/bin/sh\necho "$@" >> "{repo}/curl-calls"\n')
    (repo / "bin" / "curl").chmod(0o755)
    (repo / ".env").write_text("WATCHDOG_WEBHOOK_URL=https://ntfy.example/topic\n")
    _watchdog(repo, home)
    assert "https://ntfy.example/topic" in (repo / "curl-calls").read_text()
