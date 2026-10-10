"""Isolated behavioral tests for launchd/install.sh."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "launchd" / "install.sh"
PLIST_NAMES = (
    "com.kelly.agent-newsletter.plist",
    "com.kelly.agent-newsletter-watchdog.plist",
)


@pytest.fixture()
def install_environment(tmp_path):
    repo = tmp_path / "repo"
    launchd_dir = repo / "launchd"
    launchd_dir.mkdir(parents=True)
    shutil.copy2(INSTALL_SCRIPT, launchd_dir / "install.sh")
    for name in PLIST_NAMES:
        (launchd_dir / name).write_text(f"fixture for {name}\n")

    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "launchctl.log"
    launchctl = bin_dir / "launchctl"
    launchctl.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$LAUNCHCTL_LOG"\n'
        'if [ "$1" = print ]; then printf "state = running\\n"; fi\n'
    )
    launchctl.chmod(0o755)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["LAUNCHCTL_LOG"] = str(log_path)
    return repo, home, env, log_path


def _run_install(repo: Path, env: dict[str, str], *args: str):
    return subprocess.run(
        ["bash", str(repo / "launchd" / "install.sh"), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


def test_install_copies_agents_and_bootstraps_each_service(install_environment):
    repo, home, env, log_path = install_environment
    target_dir = home / "Library" / "LaunchAgents"

    result = _run_install(repo, env)

    for name in PLIST_NAMES:
        assert (target_dir / name).read_text() == f"fixture for {name}\n"
        label = name.removesuffix(".plist")
        assert f"bootout gui/{os.getuid()}/{label}" in result.stdout or (
            f"bootout gui/{os.getuid()}/{label}" in log_path.read_text()
        )
        assert f"bootstrap gui/{os.getuid()} {target_dir / name}" in log_path.read_text()
        assert f"print gui/{os.getuid()}/{label}" in log_path.read_text()

    assert "installed" in result.stdout
    assert result.stdout.count("running") == len(PLIST_NAMES)


def test_uninstall_removes_agents_and_unloads_services(install_environment):
    repo, home, env, log_path = install_environment
    target_dir = home / "Library" / "LaunchAgents"
    target_dir.mkdir(parents=True)
    for name in PLIST_NAMES:
        (target_dir / name).write_text("installed fixture\n")

    result = _run_install(repo, env, "--uninstall")

    assert "all agents uninstalled" in result.stdout
    for name in PLIST_NAMES:
        label = name.removesuffix(".plist")
        assert not (target_dir / name).exists()
        assert f"unload {target_dir / name}" in log_path.read_text()
        assert f"bootout gui/{os.getuid()}/{label}" in log_path.read_text()


def test_missing_source_plist_is_skipped_without_installing(install_environment):
    repo, home, env, log_path = install_environment
    source = repo / "launchd" / PLIST_NAMES[1]
    source.unlink()

    result = _run_install(repo, env)

    assert f"skip: {source} not found" in result.stdout
    assert not (home / "Library" / "LaunchAgents" / PLIST_NAMES[1]).exists()
    missing_target = home / "Library" / "LaunchAgents" / PLIST_NAMES[1]
    assert f"bootstrap gui/{os.getuid()} {missing_target}" not in log_path.read_text()
