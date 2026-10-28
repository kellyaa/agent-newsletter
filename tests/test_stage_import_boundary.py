"""Pipeline stage scripts must not import from each other (AGENTS.md, CONTRIBUTING.md).

Stages share state only through state.db and the shared modules. A stage that
imports another runs the other's module-level setup (edition load, regex
compilation, logging config) and silently couples the two: moving a constant
in one breaks the other. Orchestration and tooling scripts (backfill.py,
replay_writer.py, check_edition.py) are exempt by design.

The rule used to be documentation only; rank.py imported prefilter's prerank
cap and scorer, and run_attempt.py imported publish's usage reader. This test
parses every script's imports so a regression fails CI.
"""
from __future__ import annotations

import ast
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# Stages run by scripts/run_edition.sh, plus the run_attempt bookkeeping
# step it calls around them.
STAGES = {"fetch", "prefilter", "rank", "write", "publish", "run_attempt"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_stage_list_matches_scripts():
    for stage in STAGES:
        assert (SCRIPTS / f"{stage}.py").is_file(), stage


def test_stages_do_not_import_each_other():
    violations = {
        stage: sorted(_imported_modules(SCRIPTS / f"{stage}.py") & (STAGES - {stage}))
        for stage in sorted(STAGES)
    }
    violations = {k: v for k, v in violations.items() if v}
    assert not violations, (
        f"stage scripts import other stages: {violations}; move the shared "
        "code into a shared module (db.py, candidates.py, edition.py, llm.py, models.py)"
    )


def test_detector_sees_both_import_forms(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("import prefilter as p\nfrom publish import usage_totals\nfrom . import rank\n")
    assert _imported_modules(p) == {"prefilter", "publish"}


def test_prefilter_and_rank_share_one_prerank_heuristic():
    """Both stages cap the papers bucket with candidates.prerank_score and the
    edition's keyword gate, so prefilter's debug artifact and per-run cut count
    describe exactly what rank sends to the LLM."""
    from datetime import datetime, timezone

    import candidates
    import edition
    import prefilter
    import rank

    now = datetime(2026, 6, 27, 12, tzinfo=timezone.utc)
    items = [
        {"published_at": "2026-06-27T00:00:00Z", "title": "LLM agent tool use", "raw_text": "agentic"},
        {"published_at": "2026-06-20T00:00:00Z", "title": "Graph theory", "raw_text": ""},
        {"published_at": "garbage", "fetched_at": None, "title": None, "raw_text": None},
    ]
    kw = edition.current().keyword_re()
    for it in items:
        expected = candidates.prerank_score(it, now, kw)
        assert rank._prerank_scorer(it, now) == expected
        assert prefilter._prerank_score(it, now) == expected
    assert rank.PAPER_PRERANK_CAP == prefilter.PAPER_PRERANK_CAP == candidates.PAPER_PRERANK_CAP
