"""run_attempts outcome vocabulary is defined once, in models.AttemptOutcome.

``run_edition.sh`` writes ``ok`` / ``failed`` through ``run_attempt.py finish``;
``run.sh`` reads ``run_attempt.py latest`` (``started | ok | failed | none``) to
skip editions that already shipped and to pick the ones ``--failed`` re-runs.
A rename on one side alone would make run.sh silently re-run every edition (or
none), so the Python side takes the words from models and these guards check
that the shell scripts still use the same ones.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import db as db_mod
import run_attempt
from models import NO_ATTEMPT, AttemptOutcome

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
RUN_SH = ROOT / "run.sh"
RUN_EDITION = SCRIPTS / "run_edition.sh"
VOCAB = {o.value for o in AttemptOutcome} | {NO_ATTEMPT}
GUARDED = ("run_attempt.py", "db.py")


def test_values_unchanged():
    # Stored in state.db on the content branch and compared by run.sh.
    assert [o.value for o in AttemptOutcome] == ["started", "ok", "failed"]
    assert NO_ATTEMPT == "none"
    assert NO_ATTEMPT not in {o.value for o in AttemptOutcome}


def _outcome_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef))
        and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
    }
    hits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Constant) or not isinstance(n.value, str) or id(n) in docstrings:
            continue
        # SQL `-- comments` (the schema documents the vocabulary) don't count.
        code = re.sub(r"--[^\n]*", "", n.value)
        if n.value in VOCAB or re.search(r"'(started|ok|failed)'", code):
            hits.append(f"{path.name}:{n.lineno} {n.value!r}")
    return hits


def test_detector_flags_inline_literals(tmp_path):
    f = tmp_path / "x.py"
    f.write_text(
        '"""doc: ok | failed"""\n'
        'a = "failed"\n'
        "b = \"INSERT INTO t VALUES (?, 'started')\"\n"
        'c = "none"\n'
        'd = "fine"\n'
        "e = \"x TEXT -- 'started' = died\"\n"
    )
    assert [h.split()[0] for h in _outcome_literals(f)] == ["x.py:2", "x.py:3", "x.py:4"]


def test_no_inline_outcome_literals():
    hits = [h for name in GUARDED for h in _outcome_literals(SCRIPTS / name)]
    assert hits == [], f"use models.AttemptOutcome / NO_ATTEMPT instead of literals: {hits}"


def test_cli_choices_come_from_models():
    with pytest.raises(SystemExit):
        run_attempt.main(["finish", "1", "started"])
    assert {AttemptOutcome.OK, AttemptOutcome.FAILED} < set(AttemptOutcome)


# ---------------------------------------------------------------------------
# Shell writers / readers agree with models
# ---------------------------------------------------------------------------

_FINISH_RE = re.compile(r'run_attempt\.py finish "\$RUN_ATTEMPT" (\w+)')
_CASE_RE = re.compile(r'case "\$latest" in ([\w|]+)\)')
_EQ_RE = re.compile(r'"\$latest" = (\w+)')
_FALLBACK_RE = re.compile(r'run_attempt\.py latest[^\n]*\|\| echo (\w+)')


def test_run_edition_finishes_with_model_outcomes():
    words = _FINISH_RE.findall(RUN_EDITION.read_text())
    assert sorted(words) == sorted([AttemptOutcome.OK.value, AttemptOutcome.FAILED.value]), words


def test_run_sh_reads_model_outcomes():
    text = RUN_SH.read_text()
    cases = [w for grp in _CASE_RE.findall(text) for w in grp.split("|")]
    eqs = _EQ_RE.findall(text)
    fallback = _FALLBACK_RE.findall(text)
    # --failed re-runs failed and died-mid-run attempts; default skips ok ones.
    assert sorted(cases) == sorted([AttemptOutcome.FAILED.value, AttemptOutcome.STARTED.value])
    assert eqs == [AttemptOutcome.OK.value]
    assert fallback == [NO_ATTEMPT]


def test_shell_detectors_find_drift():
    assert _FINISH_RE.findall('scripts/run_attempt.py finish "$RUN_ATTEMPT" success') == ["success"]
    assert _CASE_RE.findall('case "$latest" in failed|died) ;;') == ["failed|died"]
    assert _FALLBACK_RE.findall("run_attempt.py latest 2>/dev/null || echo missing") == ["missing"]


def test_roundtrip_matches_what_run_sh_compares(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    db_mod.init_db(path)
    monkeypatch.setattr(run_attempt, "code_state", lambda: ("abc", 0))
    conn = db_mod.connect(path)
    try:
        day = "2026-11-08"
        assert run_attempt.latest_outcome(conn, day) == NO_ATTEMPT
        n = run_attempt.start(conn, day, "t")
        assert run_attempt.latest_outcome(conn, day) == AttemptOutcome.STARTED
        run_attempt.finish(conn, day, n, AttemptOutcome.FAILED.value, "rank", 1, None)
        assert run_attempt.latest_outcome(conn, day) == AttemptOutcome.FAILED
        n = run_attempt.start(conn, day, "t")
        run_attempt.finish(conn, day, n, AttemptOutcome.OK.value, None, 0, None)
        assert run_attempt.latest_outcome(conn, day) == AttemptOutcome.OK
        assert type(run_attempt.latest_outcome(conn, day)) is str
    finally:
        conn.close()
