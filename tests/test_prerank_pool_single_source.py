"""The papers pre-rank cap is applied only through candidates.load_ranking_pool.

prefilter.main and rank.main each passed ``prerank_cap=PAPER_PRERANK_CAP`` and
their own ``prerank_score`` wrapper to ``load_candidates_from_db``, while
backfill.main called ``load_candidates_from_db(conn)`` with no cap at all. So
a missed-day backfill, which is exactly the case with a backlog, sent every
unscored paper to the ranker in one call. rank.main warns that this runs past
the endpoint's timeout. It also ranked a different set of papers than the
daily run would have.

These tests pin the cap to one loader and fail if a caller wires it by hand
again, or loads the ranking pool without it.
"""
from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import candidates

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_CAP_KWARGS = {"prerank_cap", "prerank_scorer"}
# Stages that load the candidate pool for ranking (or to describe it).
_POOL_LOADERS = ("prefilter.py", "rank.py", "backfill.py")


def _calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _cap_wiring(path: Path) -> list[str]:
    """``<line>: <kwarg>`` for every call in *path* that passes a cap kwarg."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        f"{c.lineno}: {kw.arg}"
        for c in _calls(tree)
        for kw in c.keywords
        if kw.arg in _CAP_KWARGS
    ]


def _main_calls(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    return {name for c in _calls(main) if (name := _name(c))}


@pytest.mark.parametrize("path", sorted(p for p in SCRIPTS.glob("*.py") if p.name != "candidates.py"),
                         ids=lambda p: p.name)
def test_no_script_wires_the_prerank_cap_itself(path):
    wired = _cap_wiring(path)
    assert not wired, (
        f"{path.name} passes {wired} itself; load the pool through "
        "candidates.load_ranking_pool so every caller gets the same cap"
    )


@pytest.mark.parametrize("name", _POOL_LOADERS)
def test_pool_loaders_use_load_ranking_pool(name):
    calls = _main_calls(SCRIPTS / name)
    assert "load_ranking_pool" in calls, f"{name}.main does not load via load_ranking_pool"
    assert "load_candidates_from_db" not in calls, (
        f"{name}.main calls load_candidates_from_db directly, which skips the pre-rank cap"
    )


def test_guard_detects_a_regression(tmp_path):
    p = tmp_path / "x.py"
    p.write_text(
        "def main():\n"
        "    load_candidates_from_db(conn, prerank_cap=5, prerank_scorer=f)\n"
    )
    assert _cap_wiring(p) == ["2: prerank_cap", "2: prerank_scorer"]
    assert "load_ranking_pool" not in _main_calls(p)


# ---------- behaviour ----------

NOW = datetime(2026, 7, 15, tzinfo=timezone.utc)


def _seed_papers(db_path: Path, n: int) -> None:
    import db as db_mod

    db_mod.init_db(db_path)
    conn = sqlite3.connect(db_path)
    for i in range(n):
        conn.execute(
            "INSERT INTO items (id, source, url, canonical_url, title, published_at, fetched_at,"
            " raw_text, status, section, first_seen_date, last_seen_date)"
            " VALUES (?, 'arxiv:cs', ?, ?, ?, ?, ?, 'abstract', 'candidate', 'papers', ?, ?)",
            (f"p{i:03d}", f"https://arxiv.org/abs/{i}", f"https://arxiv.org/abs/{i}",
             f"paper {i}", f"2026-07-{1 + i % 14:02d}", "2026-07-14",
             "2026-07-14", "2026-07-14"),
        )
    conn.commit()
    conn.close()


def test_load_ranking_pool_caps_unscored_papers(tmp_path):
    import re

    import db as db_mod

    db_path = tmp_path / "state.db"
    _seed_papers(db_path, candidates.PAPER_PRERANK_CAP + 7)
    conn = db_mod.connect(db_path)
    stats: dict[str, int] = {}
    pool = candidates.load_ranking_pool(conn, now=NOW, keyword_re=re.compile("paper"), stats=stats)
    conn.close()
    assert len(pool["papers"]) == candidates.PAPER_PRERANK_CAP
    assert stats == {"prerank_cut": 7}


def test_bound_prerank_scorer_matches_prerank_score():
    import re

    kw = re.compile("agent", re.IGNORECASE)
    item = {"published_at": "2026-07-14T00:00:00Z", "title": "Agent tools", "raw_text": "agent"}
    assert candidates.bound_prerank_scorer(kw)(item, NOW) == candidates.prerank_score(item, NOW, kw)


def test_backfill_ranks_a_capped_pool(tmp_path, monkeypatch):
    """A sandbox pool larger than the cap reaches the ranker capped, scored at --date."""
    import backfill as bf

    sandbox_db = tmp_path / "state.db"
    _seed_papers(sandbox_db, candidates.PAPER_PRERANK_CAP + 12)

    monkeypatch.setattr(bf, "setup_sandbox", lambda date: tmp_path)
    monkeypatch.setattr(bf, "age_out_for_synthetic_date", lambda conn, date: 0)
    seen: dict = {}

    def fake_rank(conn, pool):
        seen["pool"] = pool
        return {}

    real_load = candidates.load_candidates_from_db

    def spy_load(conn=None, **kw):
        seen["now"] = kw.get("now")
        return real_load(conn, **kw)

    monkeypatch.setattr(candidates, "load_candidates_from_db", spy_load)
    monkeypatch.setattr(bf, "rank_with_optional_llm", fake_rank)
    monkeypatch.setattr(bf, "run_writer_for_date", lambda conn, date, force: None)
    monkeypatch.setattr("sys.argv", ["backfill.py", "--date", "2026-07-15"])

    assert bf.main() == 1  # writer stubbed to write nothing
    assert len(seen["pool"]["papers"]) == candidates.PAPER_PRERANK_CAP
    assert seen["now"] == NOW
