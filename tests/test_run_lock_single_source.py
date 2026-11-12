"""The single-run lock contract lives only in ``scripts/runlock.sh``.

``run.sh`` holds ``.watchdog.lock`` for the life of a run, and
``scripts/watchdog.sh`` probes it (and holds its own ``.watchdog-self.lock``).
Each script used to spell the path and the ``flock -n`` call by hand, and
neither could tell a held lock from a missing ``flock`` (stock macOS has none).
So run.sh exited 0 without running, and the watchdog reported
``STATUS: IN_PROGRESS`` every day for a run that never existed (#662).
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "runlock.sh"
SHELL = sorted({*REPO.glob("*.sh"), *(REPO / "scripts").glob("*.sh"),
                *(REPO / "launchd").glob("*.sh")} - {LIB})


def _no_flock_path(tmp_path: Path) -> str:
    """A PATH with every command on the current PATH except flock."""
    d = tmp_path / "noflock-bin"
    d.mkdir()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(entry)
        if not p.is_dir():
            continue
        for f in p.iterdir():
            if f.name == "flock" or (d / f.name).exists():
                continue
            if f.is_file() and os.access(f, os.X_OK):
                (d / f.name).symlink_to(f)
    assert not (d / "flock").exists()
    return str(d)


def _sh(script: str, *args: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **({"PATH": path} if path else {})}
    return subprocess.run(
        ["bash", "-c", f'. "{LIB}"; {script}', "_", *args],
        capture_output=True, text=True, timeout=10, env=env,
    )


# ─── single source ─────────────────────────────────────────────────────────

def _code(p: Path) -> str:
    return "\n".join(l for l in p.read_text().splitlines() if not l.lstrip().startswith("#"))


@pytest.mark.parametrize("script", SHELL, ids=lambda p: str(p.relative_to(REPO)))
def test_no_script_calls_flock_directly(script):
    # A flock invocation (`flock -n 9`, `flock 9`); messages naming it are fine.
    assert not re.search(r"(?<![\w-])flock\s+(-|\d)", _code(script)), (
        f"{script.name} calls flock; use runlock_acquire/runlock_held from scripts/runlock.sh")


@pytest.mark.parametrize("script", SHELL, ids=lambda p: str(p.relative_to(REPO)))
def test_no_script_spells_the_lock_paths(script):
    assert ".watchdog.lock" not in _code(script)
    assert ".watchdog-self.lock" not in _code(script)


def test_users_source_the_library():
    for p in (REPO / "run.sh", REPO / "scripts" / "watchdog.sh"):
        assert '. "$REPO_ROOT/scripts/runlock.sh"' in p.read_text(), p.name


def test_lock_paths_are_gitignored():
    r = _sh('runlock_run_path "$1"; runlock_self_path "$1"', "/r")
    run_lock, self_lock = r.stdout.split()
    ignored = (REPO / ".gitignore").read_text().splitlines()
    assert Path(run_lock).name in ignored and Path(self_lock).name in ignored


# ─── library behaviour ─────────────────────────────────────────────────────

def test_acquire_then_probe_from_another_process(tmp_path):
    lock = tmp_path / "l.lock"
    assert _sh('runlock_held "$1"', str(lock)).returncode == 1  # free
    with lock.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _sh('runlock_held "$1"', str(lock)).returncode == 0
        assert _sh('runlock_acquire 9 "$1"', str(lock)).returncode == 1
    r = _sh('runlock_acquire 9 "$1" || exit $?; runlock_held "$1"; echo "probe=$?"', str(lock))
    # The holder's own probe opens a fresh descriptor, so it sees the lock as held.
    assert r.returncode == 0 and r.stdout.strip() == "probe=0"


def test_probe_never_retains_the_lock(tmp_path):
    lock = tmp_path / "l.lock"
    r = _sh('runlock_held "$1"; runlock_held "$1"; echo "$?"', str(lock))
    assert r.stdout.strip() == "1"
    with lock.open("w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # still free afterwards


def test_acquire_rejects_a_bad_fd(tmp_path):
    r = _sh('runlock_acquire "9; echo pwned" "$1"', str(tmp_path / "l.lock"))
    assert r.returncode == 64 and "pwned" not in r.stdout


def test_missing_flock_is_its_own_result(tmp_path):
    path = _no_flock_path(tmp_path)
    lock = tmp_path / "l.lock"
    assert _sh("runlock_available", path=path).returncode == 1
    assert _sh('runlock_held "$1"', str(lock), path=path).returncode == 2
    assert _sh('runlock_acquire 9 "$1"', str(lock), path=path).returncode == 2
    assert not lock.exists()  # nothing opened when the lock cannot be taken


# ─── callers fail loudly without flock ─────────────────────────────────────

def test_run_sh_without_flock_fails_instead_of_exiting_0(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "run.sh", repo / "run.sh")
    for lib in ("runlog.sh", "runlock.sh", "contentbranch.sh"):
        shutil.copy2(REPO / "scripts" / lib, repo / "scripts" / lib)
    p = subprocess.run(["bash", str(repo / "run.sh")], cwd=repo, capture_output=True, text=True,
                       timeout=30, env={**os.environ, "PATH": _no_flock_path(tmp_path)})
    assert p.returncode == 1
    assert "flock not found" in p.stdout
    assert "another run holds" not in p.stdout
    assert not (repo / ".worktrees").exists()


def test_watchdog_without_flock_is_failed_not_in_progress(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    for f in ("watchdog.sh", "runlog.sh", "runlock.sh", "contentbranch.sh", "edition.py", "models.py"):
        shutil.copy2(REPO / "scripts" / f, repo / "scripts" / f)
    (repo / "run.sh").write_text("#!/usr/bin/env bash\necho called >> run-calls.txt\n")
    (repo / "editions" / "agents").mkdir(parents=True)
    shutil.copy2(REPO / "editions" / "agents" / "edition.yaml",
                 repo / "editions" / "agents" / "edition.yaml")
    env = {**os.environ, "PATH": _no_flock_path(tmp_path), "NEWSLETTER_PYTHON": sys.executable}
    # --check probes the run lock too, so it reports the same cause.
    for args in ([], ["--check"]):
        p = subprocess.run(["bash", str(repo / "scripts" / "watchdog.sh"), *args], cwd=repo,
                           capture_output=True, text=True, timeout=60, env=env)
        last = p.stdout.strip().splitlines()[-1]
        assert p.returncode == 1, p.stdout + p.stderr
        assert re.fullmatch(r"STATUS: FAILED \d{4}-\d\d-\d\d no-flock", last)
        assert "IN_PROGRESS" not in p.stdout
        assert "command not found" not in p.stderr
    assert not (repo / "run-calls.txt").exists()
