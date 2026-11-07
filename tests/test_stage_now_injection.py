"""Stage ``main(now=...)`` contract: the wall clock is injectable.

fetch, prefilter and rank each take an optional ``now`` so tests and
backfills pin the instant instead of patching ``datetime``. These tests pin
that the injected instant actually reaches the code that reads the clock,
and an AST guard keeps the parameter on every time-dependent stage.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from conftest import base_template

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
FROZEN_NOW = datetime(2026, 7, 9, 12, 34, 56, tzinfo=timezone.utc)

# Stages whose main() reads the wall clock and must accept it injected.
NOW_STAGES = ("fetch", "prefilter", "rank")


@pytest.mark.parametrize("stage", NOW_STAGES)
def test_stage_main_accepts_optional_now(stage):
    tree = ast.parse((SCRIPTS / f"{stage}.py").read_text())
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(mains) == 1
    args = mains[0].args
    names = [a.arg for a in args.args]
    assert names == ["now"], f"{stage}.main signature drifted: {names}"
    assert len(args.defaults) == 1
    assert isinstance(args.defaults[0], ast.Constant) and args.defaults[0].value is None


def test_fetch_main_stamps_injected_now(tmp_path, monkeypatch):
    import db as db_mod
    import fetch as fetch_mod

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    sources = tmp_path / "sources.yaml"
    sources.write_text("rss:\n  - id: blog\n    url: https://example.com/feed.xml\n")
    item = fetch_mod.Item(
        source="rss:blog", url="https://example.com/1", title="T",
        author=None, published_at=None, raw_text="body",
    )
    monkeypatch.setattr(fetch_mod, "sources_path", lambda: sources)
    monkeypatch.setattr(fetch_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(fetch_mod, "init_db", lambda: db_mod.init_db(db_path))
    monkeypatch.setattr(fetch_mod, "fetch_rss", lambda src, **kw: [item])

    assert fetch_mod.main(now=FROZEN_NOW) == 0

    conn = db_mod.connect(db_path)
    rows = conn.execute("SELECT fetched_at FROM items").fetchall()
    conn.close()
    assert [r["fetched_at"] for r in rows] == [FROZEN_NOW.isoformat()]


def test_rank_main_passes_injected_now_to_pool_loader(tmp_path, monkeypatch):
    import db as db_mod
    import rank as rank_mod

    db_path = tmp_path / "state.db"
    db_mod.init_db(db_path)
    prompt = tmp_path / "rank.md"
    prompt.write_text(base_template("rank"))
    monkeypatch.setattr(rank_mod, "PROMPT_PATH", prompt)
    monkeypatch.setattr(rank_mod, "RANKED_PATH", tmp_path / "ranked.json")
    monkeypatch.setattr(rank_mod, "connect", lambda: db_mod.connect(db_path))
    monkeypatch.setattr(rank_mod, "init_db", lambda: db_mod.init_db(db_path))

    seen = {}

    def fake_load(**kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(rank_mod, "load_candidates_from_db", fake_load)

    assert rank_mod.main(now=FROZEN_NOW) == 0
    assert seen["now"] is FROZEN_NOW
