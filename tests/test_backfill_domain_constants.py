"""backfill.py takes its section and status names from models, not string literals.

backfill.py used to spell out "papers"/"news"/"blogs" and "featured"/"appendix"/...
inline when it bucketed scored items, iterated sections for the ranker and
filtered featured ids. Adding or renaming a section in models.Section would
then leave a backfill silently skipping that section's unscored items. This
guard fails if a bare literal comes back. Docstrings are ignored;
"papers_prescored" is a candidate bucket, not a section, so it is allowed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from models import Section, Status

BACKFILL_PY = Path(__file__).resolve().parent.parent / "scripts" / "backfill.py"
DOMAIN_NAMES = {s.value for s in Section} | {s.value for s in Status}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def test_backfill_has_no_bare_section_or_status_literals():
    tree = ast.parse(BACKFILL_PY.read_text())
    skip = _docstring_nodes(tree)
    offenders = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and node.value in DOMAIN_NAMES
    ]
    assert offenders == [], f"use models.Section/Status instead of literals: {offenders}"


def test_rank_with_optional_llm_ranks_every_models_section(monkeypatch):
    """Each section in models.Section with unscored items goes to the ranker,
    and prescored papers land in the papers bucket."""
    import backfill
    import prompts
    import rank as rank_mod

    invoked: list[str] = []
    captured: dict = {}

    monkeypatch.setattr(prompts, "render", lambda *a, **k: "rubric")
    monkeypatch.setattr(rank_mod, "build_prompt", lambda section, items, rubric: section)

    def fake_invoke(prompt, label):
        invoked.append(label)
        return [{"id": f"{label}-1", "score": 5, "tags": [], "why": "x"}]

    def fake_assign(scored_by_section):
        captured.update(scored_by_section)
        return {}

    monkeypatch.setattr(rank_mod, "invoke_ranker", fake_invoke)
    monkeypatch.setattr(rank_mod, "sanitize_rankings", lambda scored, ids, section: scored)
    monkeypatch.setattr(rank_mod, "assign_statuses", fake_assign)
    monkeypatch.setattr(rank_mod, "persist", lambda conn, decisions: {})

    candidates = {s.value: [{"id": f"{s.value}-1"}] for s in Section}
    candidates["papers_prescored"] = [{"id": "pp", "score": 7}]

    decisions = backfill.rank_with_optional_llm(None, candidates)
    assert invoked == [s.value for s in Section]
    assert set(captured) == {s.value for s in Section}
    assert sorted(it["id"] for it in captured[Section.PAPERS]) == ["papers-1", "pp"]
    # fake_assign decided nothing, so every candidate takes the shared
    # unscored -> appendix fallback, keyed by its models.Section.
    assert {iid: d["section"] for iid, d in decisions.items()} == {
        **{f"{s.value}-1": s.value for s in Section}, "pp": Section.PAPERS.value,
    }
    assert {d["status"] for d in decisions.values()} == {Status.APPENDIX}


@pytest.mark.parametrize("section", list(Section))
def test_scored_buckets_are_plain_str_keys(section, monkeypatch):
    """Bucket keys stay plain str so JSON dumps / logs print 'papers', not
    'Section.PAPERS'."""
    import backfill
    import prompts
    import rank as rank_mod

    captured: dict = {}
    monkeypatch.setattr(prompts, "render", lambda *a, **k: "rubric")
    monkeypatch.setattr(rank_mod, "assign_statuses", lambda s: captured.update(s) or {})
    monkeypatch.setattr(rank_mod, "persist", lambda conn, decisions: {})
    backfill.rank_with_optional_llm(None, {})
    assert type(next(k for k in captured if k == section.value)) is str
