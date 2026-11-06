"""The ranker scoring core lives in rank.score_candidates; backfill reuses it.

backfill.rank_with_optional_llm used to rebuild the per-section loop itself:
it called rank.invoke_ranker directly instead of rank.rank_section, and it
had no unscored -> appendix fallback. So a backfill whose snapshot held one
content-filtered item lost the whole section (no bisection), one malformed
LLM reply (RuntimeError) aborted the backfill instead of leaving the batch
unscored, and any candidate the ranker omitted stayed 'candidate' instead of
landing in the appendix. Every fix to the daily path (#208 isolation,
content-filter bisection, #290 sanitising) had to be ported by hand.

These tests pin the shared core and fail if backfill drifts off it again.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from models import Section, Status

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

# Scoring internals that only rank.score_candidates may orchestrate.
_CORE_CALLS = {"invoke_ranker", "rank_section", "sanitize_rankings", "assign_statuses", "build_prompt"}


def _attr_calls(path: Path, module_alias: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == module_alias
    }


def test_backfill_calls_score_candidates_not_the_internals():
    calls = _attr_calls(SCRIPTS / "backfill.py", "rank_mod")
    assert "score_candidates" in calls
    leaked = sorted(calls & _CORE_CALLS)
    assert not leaked, (
        f"backfill.py calls rank internals {leaked}; route scoring through "
        "rank.score_candidates so it shares bisection, failure isolation and "
        "the appendix fallback with the daily run"
    )


def test_rank_main_uses_score_candidates():
    tree = ast.parse((SCRIPTS / "rank.py").read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    called = {
        n.func.id for n in ast.walk(main)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "score_candidates" in called
    assert not called & _CORE_CALLS, sorted(called & _CORE_CALLS)


def test_guard_detects_a_regression(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("rank_mod.invoke_ranker(p, label='papers')\nrank_mod.score_candidates(c, r)\n")
    assert _attr_calls(p, "rank_mod") & _CORE_CALLS == {"invoke_ranker"}


# ---------- behaviour backfill now shares with the daily run ----------

def _paper(pid: str) -> dict:
    return {"id": pid, "title": f"title-{pid}", "url": f"https://x.test/{pid}",
            "source": "arxiv:cs", "author": "A", "published_at": "2026-07-01",
            "raw_text": "abstract"}


@pytest.fixture
def no_persist(monkeypatch):
    import prompts
    import rank as rank_mod
    monkeypatch.setattr(prompts, "render", lambda *a, **k: "rubric")
    monkeypatch.setattr(rank_mod, "persist", lambda conn, decisions: {})


def test_backfill_bisects_content_filtered_item(monkeypatch, no_persist):
    import backfill
    import rank as rank_mod
    from llm import ContentFilterError

    def fake_invoke(prompt, label):
        if "title-bad" in prompt:
            raise ContentFilterError("blocked")
        return [{"id": pid, "score": 9, "tags": [], "why": "ok"}
                for pid in ("a", "b", "c") if f"title-{pid}" in prompt]

    monkeypatch.setattr(rank_mod, "invoke_ranker", fake_invoke)
    grouped = {"papers": [_paper("a"), _paper("bad"), _paper("b"), _paper("c")],
               "papers_prescored": [], "news": [], "blogs": []}

    decisions = backfill.rank_with_optional_llm(None, grouped)

    assert {i: d["score"] for i, d in decisions.items()} == {"a": 9, "b": 9, "c": 9, "bad": 0}
    assert decisions["bad"]["status"] == Status.APPENDIX


def test_backfill_survives_ranker_runtime_error(monkeypatch, no_persist):
    import backfill
    import rank as rank_mod

    def fake_invoke(prompt, label):
        raise RuntimeError("llm returned non-JSON content")

    monkeypatch.setattr(rank_mod, "invoke_ranker", fake_invoke)
    grouped = {"papers": [_paper("u1")],
               "papers_prescored": [{"id": "p1", "score": 9, "tags": [], "why": "cached"}],
               "news": [], "blogs": []}

    decisions = backfill.rank_with_optional_llm(None, grouped)

    assert decisions["p1"]["score"] == 9
    assert decisions["u1"]["status"] == Status.APPENDIX
    assert decisions["u1"]["section"] == Section.PAPERS


def test_backfill_omitted_item_falls_back_to_appendix(monkeypatch, no_persist):
    import backfill
    import rank as rank_mod

    monkeypatch.setattr(rank_mod, "invoke_ranker", lambda prompt, label: [])
    grouped = {"papers": [], "papers_prescored": [],
               "news": [_paper("n1")], "blogs": []}

    decisions = backfill.rank_with_optional_llm(None, grouped)

    assert decisions == {"n1": {
        "status": Status.APPENDIX, "score": 0, "tags": [],
        "why": "fallback: ranker did not return a score", "section": Section.NEWS,
    }}


def test_score_candidates_writes_ranked_path_only_when_given(tmp_path, monkeypatch):
    import json

    import rank as rank_mod

    monkeypatch.setattr(rank_mod, "invoke_ranker", lambda prompt, label: [
        {"id": "a", "score": 9, "tags": [], "why": "ok"}])
    grouped = {"papers": [_paper("a")], "papers_prescored": [], "news": [], "blogs": []}

    rank_mod.score_candidates(grouped, "rubric")
    assert not any(tmp_path.iterdir())

    out = tmp_path / "sub" / "ranked.json"
    rank_mod.score_candidates(grouped, "rubric", ranked_path=out)
    assert [e["id"] for e in json.loads(out.read_text())["papers"]] == ["a"]
