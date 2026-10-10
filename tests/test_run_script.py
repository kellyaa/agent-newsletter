"""Tests for run.sh argument validation and single-run locking."""
from __future__ import annotations

import fcntl
import subprocess
from pathlib import Path


RUN_SCRIPT = Path(__file__).resolve().parents[1] / "run.sh"


def _run_script(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "run.sh"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _copy_run_script(repo: Path) -> None:
    (repo / "run.sh").write_bytes(RUN_SCRIPT.read_bytes())


def test_help_prints_usage_and_exits_before_creating_logs(tmp_path):
    _copy_run_script(tmp_path)

    result = _run_script(tmp_path, "--help")

    assert result.returncode == 0
    assert "Usage:" in result.stdout
    assert not (tmp_path / "logs").exists()


def test_unknown_argument_fails_before_creating_logs(tmp_path):
    _copy_run_script(tmp_path)

    result = _run_script(tmp_path, "--unknown")

    assert result.returncode == 2
    assert "unknown arg: --unknown" in result.stderr
    assert not (tmp_path / "logs").exists()


def test_existing_run_lock_exits_without_starting_pipeline(tmp_path):
    _copy_run_script(tmp_path)
    lock_path = tmp_path / ".watchdog.lock"

    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        result = _run_script(tmp_path)

    assert result.returncode == 0
    assert "another run holds" in result.stdout
    assert not (tmp_path / ".worktrees").exists()
