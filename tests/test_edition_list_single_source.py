"""The edition-list contract lives only in ``scripts/editionlist.sh`` (#670).

``run.sh`` and ``scripts/watchdog.sh`` each read ``scripts/edition.py list``
through ``done < <(...)``. A failure inside process substitution trips neither
``set -e`` nor the ERR trap. So when ``edition.py list`` crashed, run.sh logged
"every edition is already ok today", wrote DONE and exited 0 with no alert. The
watchdog hid the error and reported ``no-editions``.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import edition  # noqa: E402
from models import EditionStatus  # noqa: E402

from test_edition_watchdog import _calls, _today, _wd  # noqa: E402
from test_edition_watchdog import repo as repo  # noqa: E402,F401  (fixture)
from test_run_multi_edition import _run  # noqa: E402
from test_run_multi_edition import fake_notifier as fake_notifier  # noqa: E402,F401
from test_run_multi_edition import fx as fx  # noqa: E402,F401

REPO = Path(__file__).resolve().parents[1]
LIB = REPO / "scripts" / "editionlist.sh"
SHELL = sorted({*REPO.glob("*.sh"), *(REPO / "scripts").glob("*.sh"),
                *(REPO / "launchd").glob("*.sh")} - {LIB})
CALLERS = (REPO / "run.sh", REPO / "scripts" / "watchdog.sh")
CRASHING_LIST = "import sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n"


def _code(p: Path) -> str:
    return "\n".join(l for l in p.read_text().splitlines() if not l.lstrip().startswith("#"))


def _sh(script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'set -euo pipefail; . "{LIB}"; {script}', "_", *args],
        capture_output=True, text=True, timeout=30, cwd=cwd or REPO,
    )


# ─── single source ─────────────────────────────────────────────────────────

def test_lib_literals_match_python():
    text = LIB.read_text()
    assert f"EDITIONLIST_PAUSED={EditionStatus.PAUSED.value}\n" in text
    assert f"EDITIONLIST_INVALID={edition.LIST_INVALID_STATUS}\n" in text
    assert edition.LIST_INVALID_STATUS not in {s.value for s in EditionStatus}


@pytest.mark.parametrize("script", SHELL, ids=lambda p: str(p.relative_to(REPO)))
def test_no_script_runs_the_list_itself(script):
    # An invocation (`"${PY_RUN[@]}" scripts/edition.py list`, `python3 …`); messages are fine.
    assert not re.search(r'(\}"?|\bpython3?|\bpython)\s+\S*edition\.py\s+list', _code(script)), (
        f"{script.name} runs `edition.py list`; use editionlist_load from scripts/editionlist.sh")


@pytest.mark.parametrize("script", SHELL, ids=lambda p: str(p.relative_to(REPO)))
def test_no_script_spells_the_skip_statuses(script):
    code = _code(script)
    for status in (EditionStatus.PAUSED.value, edition.LIST_INVALID_STATUS):
        assert not re.search(rf'=\s*"?{status}\b', code), (
            f"{script.name} compares a status to {status!r}; use editionlist_is_* instead")


@pytest.mark.parametrize("script", CALLERS, ids=lambda p: p.name)
def test_callers_source_the_library(script):
    assert '. "$REPO_ROOT/scripts/editionlist.sh"' in script.read_text()
    assert "editionlist_load" in _code(script)


# ─── library behaviour ─────────────────────────────────────────────────────

def test_load_parses_the_real_list():
    r = _sh('editionlist_load "$1"; for s in "${EDITION_SLUGS[@]}"; do '
            'printf "%s=%s\\n" "$s" "${EDITION_STATUS_OF[$s]}"; done', sys.executable)
    assert r.returncode == 0, r.stderr
    want = [f"{slug}={ed.status if ed else edition.LIST_INVALID_STATUS}"
            for slug, ed, _ in edition.discover()]
    assert r.stdout.split() == want


def test_load_failure_returns_its_status_even_under_set_e(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "edition.py").write_text(CRASHING_LIST)
    r = _sh('rc=0; editionlist_load "$1" || rc=$?; echo "rc=$rc n=${#EDITION_SLUGS[@]}"',
            sys.executable, cwd=tmp_path)
    assert r.stdout.strip() == "rc=3 n=0"
    assert "boom" in r.stderr and "edition.py list failed" in r.stderr


def test_load_failure_aborts_a_set_e_caller(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "edition.py").write_text(CRASHING_LIST)
    r = _sh('editionlist_load "$1"; echo reached', sys.executable, cwd=tmp_path)
    assert r.returncode == 3 and "reached" not in r.stdout


def test_status_predicates():
    r = _sh('for s in paused invalid live draft; do '
            'editionlist_is_paused "$s" && echo "p:$s"; editionlist_is_invalid "$s" && echo "i:$s"; '
            'done; true')
    assert r.stdout.split() == ["p:paused", "i:invalid"]


# ─── callers fail loudly when the list fails ───────────────────────────────

def test_run_sh_fails_and_alerts_when_the_list_fails(fx, fake_notifier):
    repo, _remote, env = fx
    (repo / "scripts" / "edition.py").write_text(CRASHING_LIST)
    r = _run(repo, env)
    assert r.returncode == 3, r.stdout
    assert "FAILED in orchestrator at stage=select-editions" in r.stdout
    assert "already ok today" not in r.stdout
    assert "[run] DONE" not in r.stdout
    assert "orchestrator stage=select-editions" in fake_notifier.read_text()


def test_watchdog_reports_edition_list_not_no_editions(repo):
    (repo / "scripts" / "edition.py").write_text(CRASHING_LIST)
    p, out = _wd(repo)
    assert p.returncode == 1
    assert out[-1] == f"STATUS: FAILED {_today()} edition-list"
    assert _calls(repo) == []
