"""``--help`` of the shell entry points prints their whole leading comment block.

These scripts used ``sed -n '3,Np' "$0"`` with a hard-coded line count, which
silently truncated the help (e.g. watchdog.sh dropped its ``Usage:`` section)
whenever the header grew. Help is now derived from the header itself.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REAL = Path(__file__).resolve().parents[1]
SCRIPTS = ["run.sh", "scripts/run_edition.sh", "scripts/watchdog.sh"]


def _header(path: Path) -> list[str]:
    """Lines 3.. of the file up to the first non-comment line."""
    out: list[str] = []
    for line in path.read_text().splitlines()[2:]:
        if not line.startswith("#"):
            break
        out.append(line)
    return out


@pytest.mark.parametrize("rel", SCRIPTS)
def test_help_prints_entire_header_comment(rel, tmp_path):
    script = REAL / rel
    for flag in ("--help", "-h"):
        p = subprocess.run(["bash", str(script), flag], cwd=tmp_path,
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        assert p.stdout.splitlines() == _header(script)


def test_watchdog_help_includes_usage_section(tmp_path):
    p = subprocess.run(["bash", str(REAL / "scripts/watchdog.sh"), "--help"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert "# Usage:" in p.stdout and "--check" in p.stdout
