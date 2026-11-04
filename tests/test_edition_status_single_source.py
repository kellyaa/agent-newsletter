"""Edition lifecycle states are defined once, in models.EditionStatus.

edition.py (validates edition.yaml `status`) and check_edition.py (escalates
lint warnings to errors for a live edition) used to carry their own "draft" /
"live" / "paused" literals. A typo such as ``== "Live"`` silently disables the
live-edition escalation; this guard keeps both modules on the enum.
"""
from __future__ import annotations

import ast
from pathlib import Path

import check_edition
import edition
from models import EditionStatus

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
STATUS_VALUES = {s.value for s in EditionStatus}
GUARDED = ("edition.py", "check_edition.py")


def test_statuses_come_from_models():
    assert edition.STATUSES == tuple(s.value for s in EditionStatus)
    assert all(type(s) is str for s in edition.STATUSES)


def test_status_values_unchanged():
    # run.sh / scripts/watchdog.sh compare against these strings; renaming one breaks them.
    assert [s.value for s in EditionStatus] == ["draft", "live", "paused"]


def test_watchdog_skips_the_paused_status():
    text = (SCRIPTS / "watchdog.sh").read_text()
    assert f'[ "$status" = {EditionStatus.PAUSED.value} ]' in text


def _status_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
        and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
    }
    return [
        f"{path.name}:{n.lineno} {n.value!r}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value in STATUS_VALUES and id(n) not in docstrings
    ]


def test_no_inline_edition_status_literals():
    hits = [h for name in GUARDED for h in _status_literals(SCRIPTS / name)]
    assert hits == [], f"use models.EditionStatus instead of literals: {hits}"


def test_live_edition_escalates_todos(monkeypatch):
    class Ed:
        status = "live"

    r = check_edition.LintResult()
    r.edition = Ed()
    assert check_edition._is_live(r)
    Ed.status = "draft"
    assert not check_edition._is_live(r)
