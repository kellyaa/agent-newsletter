"""Validate the committed launchd plists against the rest of the repo.

test_launchd_install.py exercises install.sh against fixture plists, so nothing
checks the real files.  A malformed plist, a label that no longer matches its
filename (launchctl bootout/print address agents by label), or a drifting
install.sh PLISTS list only fails on the operator's Mac at install time.
"""
from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHD_DIR = REPO_ROOT / "launchd"
INSTALL_SCRIPT = LAUNCHD_DIR / "install.sh"
PLIST_PATHS = sorted(LAUNCHD_DIR.glob("*.plist"))

# plist -> repo-relative script it launches
EXPECTED_SCRIPTS = {
    "com.kelly.agent-newsletter.plist": "run.sh",
    "com.kelly.agent-newsletter-watchdog.plist": "watchdog.sh",
}


def _load(path: Path) -> dict:
    with path.open("rb") as fh:
        return plistlib.load(fh)


def test_expected_plists_are_present():
    assert {p.name for p in PLIST_PATHS} == set(EXPECTED_SCRIPTS)


@pytest.mark.parametrize("path", PLIST_PATHS, ids=lambda p: p.name)
def test_plist_parses_and_label_matches_filename(path):
    assert _load(path)["Label"] == path.stem


@pytest.mark.parametrize("path", PLIST_PATHS, ids=lambda p: p.name)
def test_plist_launches_a_script_that_exists_in_repo(path):
    args = _load(path)["ProgramArguments"]
    script = Path(args[-1])
    expected = EXPECTED_SCRIPTS[path.name]
    assert script.name == expected
    repo_script = REPO_ROOT / expected
    assert repo_script.is_file()
    assert os.access(repo_script, os.X_OK), f"{expected} must be executable"


@pytest.mark.parametrize("path", PLIST_PATHS, ids=lambda p: p.name)
def test_plist_does_not_run_on_load(path):
    # `launchctl bootstrap` must not trigger a surprise pipeline run.
    assert _load(path)["RunAtLoad"] is False


@pytest.mark.parametrize("path", PLIST_PATHS, ids=lambda p: p.name)
def test_plist_has_exactly_one_schedule_key(path):
    data = _load(path)
    schedules = {"StartCalendarInterval", "StartInterval"} & set(data)
    assert len(schedules) == 1


def test_daily_plist_schedule_is_a_valid_time():
    sched = _load(LAUNCHD_DIR / "com.kelly.agent-newsletter.plist")["StartCalendarInterval"]
    assert 0 <= sched["Hour"] <= 23
    assert 0 <= sched["Minute"] <= 59


def test_watchdog_interval_is_positive():
    data = _load(LAUNCHD_DIR / "com.kelly.agent-newsletter-watchdog.plist")
    assert data["StartInterval"] > 0


@pytest.mark.parametrize("path", PLIST_PATHS, ids=lambda p: p.name)
def test_plist_paths_share_one_checkout_root(path):
    """Script and log paths must live under the same checkout directory."""
    data = _load(path)
    script_root = Path(data["ProgramArguments"][-1]).parent
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert Path(data[key]).parent.parent == script_root


def test_install_script_plist_list_matches_directory():
    text = INSTALL_SCRIPT.read_text()
    block = re.search(r"PLISTS=\((.*?)\)", text, re.DOTALL).group(1)
    listed = set(re.findall(r'"([^"]+\.plist)"', block))
    assert listed == {p.name for p in PLIST_PATHS}
